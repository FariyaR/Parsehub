import { NextRequest, NextResponse } from 'next/server'
import * as path from 'path'
import * as fs from 'fs'
import { execSync } from 'child_process'
import axios from 'axios'

const API_KEY = process.env.PARSEHUB_API_KEY || ''
const BASE_URL = process.env.PARSEHUB_BASE_URL || 'https://www.parsehub.com/api/v2'

// Parse CSV string to JSON records
function parseCSV(csvText: string): any[] {
  const lines = csvText.trim().split('\n')
  if (lines.length === 0) return []

  // Parse header
  const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''))

  // Parse rows
  const records = []
  for (let i = 1; i < lines.length; i++) {
    if (!lines[i].trim()) continue

    // Simple CSV parsing - handles quoted fields
    const values: string[] = []
    let current = ''
    let inQuotes = false

    for (let j = 0; j < lines[i].length; j++) {
      const char = lines[i][j]
      if (char === '"') {
        inQuotes = !inQuotes
      } else if (char === ',' && !inQuotes) {
        values.push(current.trim().replace(/^"|"$/g, ''))
        current = ''
      } else {
        current += char
      }
    }
    values.push(current.trim().replace(/^"|"$/g, ''))

    // Create record object
    const record: any = {}
    headers.forEach((header, index) => {
      record[header] = values[index] || ''
    })
    records.push(record)
  }

  return records
}

// Store analytics data to SQLite database SYNCHRONOUSLY
async function storeAnalyticsDataToDB(
  projectToken: string,
  runToken: string,
  analyticsData: any,
  records: any[],
  csvData?: string
): Promise<{ success: boolean; message: string; error?: string }> {
  try {
    console.log(`[DB STORE] Starting storage for project ${projectToken}, records: ${records.length}`)

    const projectRoot = path.resolve(process.cwd(), '..')
    const backendDir = path.join(projectRoot, 'backend')
    const pythonExe = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')

    if (!fs.existsSync(pythonExe)) {
      const msg = `Python exe not found at ${pythonExe}`
      console.error(`[DB STORE] ${msg}`)
      return { success: false, message: msg, error: msg }
    }

    if (!fs.existsSync(backendDir)) {
      const msg = `Backend dir not found at ${backendDir}`
      console.error(`[DB STORE] ${msg}`)
      return { success: false, message: msg, error: msg }
    }

    // Create a temporary JSON file with the data to store
    // Custom replacer to handle non-serializable objects
    const jsonReplacer = (_key: any, value: any) => {
      if (value instanceof Date) {
        return value.toISOString()
      }
      if (typeof value === 'function') {
        return undefined
      }
      if (typeof value === 'bigint') {
        return value.toString()
      }
      return value
    }


    const dataToStore = {
      project_token: projectToken,
      run_token: runToken,
      analytics_data: analyticsData,
      records: records,
      csv_data: csvData || null,
    }

    const tempFile = path.join(backendDir, `.analytics_temp_${projectToken}_${Date.now()}.json`)
    console.log(`[DB STORE] Writing temp file: ${tempFile}`)

    try {
      const jsonString = JSON.stringify(dataToStore, jsonReplacer)
      if (!jsonString || jsonString.length === 0) {
        throw new Error('JSON serialization produced empty output')
      }
      fs.writeFileSync(tempFile, jsonString)
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e)
      console.error(`[DB STORE] JSON serialization error: ${errMsg}`)
      throw e
    }

    const fileExists = fs.existsSync(tempFile)
    const fileSize = fileExists ? fs.statSync(tempFile).size : 0
    console.log(`[DB STORE] Temp file written: ${fileExists ? `verified (${fileSize} bytes)` : 'FAILED'}`)

    try {
      const pythonScript = `
import sys
import json
import os

sys.path.insert(0, '.')
from database import ParseHubDatabase

def main():
    try:
        # Get file path from command line argument
        if len(sys.argv) < 2:
            raise ValueError("No temp file path provided")
        
        temp_file = sys.argv[1]
        
        # Validate file exists and has content
        if not os.path.exists(temp_file):
            raise FileNotFoundError(f"Temp file not found: {temp_file}")
        
        file_size = os.path.getsize(temp_file)
        if file_size == 0:
            raise ValueError(f"Temp file is empty: {temp_file}")
        
        print(f"[PYTHON] Reading temp file: {temp_file} ({file_size} bytes)", file=sys.stderr)
        
        # Read and parse JSON with error handling
        try:
            with open(temp_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    raise ValueError("File content is empty")
                data = json.loads(content)
        except json.JSONDecodeError as je:
            raise ValueError(f"JSON decode error: {str(je)}")
        
        print(f"[PYTHON] Loaded data with {len(data.get('records', []))} records", file=sys.stderr)
        
        # Store to database
        db = ParseHubDatabase()
        try:
            result = db.store_analytics_data(
                data['project_token'],
                data['run_token'],
                data['analytics_data'],
                data['records'],
                data.get('csv_data')
            )
            
            print(f"[PYTHON] Store result: {result}", file=sys.stderr)
            
            if result:
                print(json.dumps({
                    "success": True,
                    "message": "Data stored successfully",
                    "records_stored": len(data.get('records', []))
                }))
            else:
                print(json.dumps({
                    "success": False,
                    "error": "Database storage returned False"
                }))
        finally:
            db.disconnect()
    
    except Exception as e:
        import traceback
        error_msg = str(e)
        print(f"[PYTHON] ERROR: {error_msg}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        print(json.dumps({
            "success": False,
            "error": error_msg
        }))
        sys.exit(1)

if __name__ == '__main__':
    main()
`

      console.log(`[DB STORE] Executing Python script with file: ${tempFile}...`)
      const output = execSync(
        `"${pythonExe}" -c "${pythonScript.replace(/"/g, '\\"')}" "${tempFile}"`,
        {
          cwd: backendDir,
          encoding: 'utf-8',
          maxBuffer: 100 * 1024 * 1024,
          stdio: ['pipe', 'pipe', 'pipe'],
          timeout: 30000,
        }
      )

      if (!output || output.trim().length === 0) {
        throw new Error('Python script produced no output')
      }

      console.log(`[DB STORE] Python output: ${output}`)
      let result
      try {
        result = JSON.parse(output)
      } catch (e) {
        console.error(`[DB STORE] Failed to parse Python output as JSON: ${output}`)
        throw new Error(`Invalid JSON from Python: ${output.substring(0, 100)}`)
      }

      if (result.success) {
        console.log(`✅ [DB STORE] SUCCESS: Data stored for ${projectToken}, records: ${result.records_stored}`)
      } else {
        console.error(`❌ [DB STORE] FAILED: ${result.error}`)
      }

      // Clean up temp file
      try {
        if (fs.existsSync(tempFile)) {
          fs.unlinkSync(tempFile)
          console.log(`[DB STORE] Temp file cleaned up`)
        }
      } catch (e) {
        console.warn(`[DB STORE] Cleanup warning:`, e instanceof Error ? e.message : e)
      }

      return result
    } finally {
      try {
        if (fs.existsSync(tempFile)) {
          fs.unlinkSync(tempFile)
        }
      } catch (e) {
        // Ignore final cleanup errors
      }
    }
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : String(error)
    console.error(`❌ [DB STORE] Exception: ${errMsg}`)
    return { success: false, message: errMsg, error: errMsg }
  }
}

// Retrieve analytics data from SQLite database
async function getAnalyticsDataFromDB(projectToken: string): Promise<any> {
  try {
    console.log(`[DB RETRIEVE] Looking for cached data for ${projectToken}...`)

    const projectRoot = path.resolve(process.cwd(), '..')
    const backendDir = path.join(projectRoot, 'backend')
    const pythonExe = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')

    if (!fs.existsSync(pythonExe)) {
      console.log(`[DB RETRIEVE] Python exe not found`)
      return null
    }

    if (!fs.existsSync(backendDir)) {
      console.log(`[DB RETRIEVE] Backend dir not found`)
      return null
    }

    const pythonScript = `
import sys
import json
sys.path.insert(0, '.')
from database import ParseHubDatabase

def main():
    try:
        # Get project token from command line argument
        if len(sys.argv) < 2:
            raise ValueError("No project token provided")
        
        project_token = sys.argv[1]
        print(f"[PYTHON] Getting analytics data for {project_token}...", file=sys.stderr)
        
        db = ParseHubDatabase()
        try:
            result = db.get_analytics_data(project_token)
            
            if result:
                records_count = len(result.get('raw_data', []))
                print(f"[PYTHON] Found data with {records_count} records", file=sys.stderr)
                # Convert datetime objects to strings for JSON serialization
                import datetime
                def default_handler(obj):
                    if isinstance(obj, datetime.datetime):
                        return obj.isoformat()
                    return str(obj)
                print(json.dumps(result, default=default_handler))
            else:
                print(f"[PYTHON] No data found in database for {project_token}", file=sys.stderr)
                print(json.dumps({"found": False}))
        finally:
            db.disconnect()
    
    except Exception as e:
        import traceback
        error_msg = str(e)
        print(f"[PYTHON] ERROR: {error_msg}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        print(json.dumps({"found": False, "error": error_msg}))
        sys.exit(1)

if __name__ == '__main__':
    main()
`

    const output = execSync(
      `"${pythonExe}" -c "${pythonScript.replace(/"/g, '\\"')}" "${projectToken}"`,
      {
        cwd: backendDir,
        encoding: 'utf-8',
        maxBuffer: 100 * 1024 * 1024,
        stdio: ['pipe', 'pipe', 'pipe'],
        timeout: 30000,
      }
    )

    if (!output || output.trim().length === 0) {
      console.log(`[DB RETRIEVE] No output from Python script`)
      return null
    }

    let result
    try {
      result = JSON.parse(output)
    } catch (e) {
      console.error(`[DB RETRIEVE] Failed to parse output as JSON: ${output}`)
      return null
    }

    if (result.found === false) {
      console.log(`[DB RETRIEVE] No cached data found for ${projectToken}`)
      return null
    }

    const recordCount = result.raw_data ? result.raw_data.length : 0
    console.log(`✅ [DB RETRIEVE] Retrieved cached data: ${recordCount} records for ${projectToken}`)
    return result
  } catch (error) {
    console.error(`❌ [DB RETRIEVE] Error retrieving analytics from DB:`, error instanceof Error ? error.message : error)
    return null
  }
}

// Fetch project data directly from ParseHub API
async function fetchProjectDataFromParseHub(projectToken: string) {
  try {
    console.log(`Fetching data for project ${projectToken} from ParseHub...`)

    // Get project info
    const projectResponse = await axios.get(`${BASE_URL}/projects/${projectToken}`, {
      params: { api_key: API_KEY },
      timeout: 15000,
    })

    const project = projectResponse.data
    if (!project) return null

    console.log(`Project found: ${project.title}, last_run status: ${project.last_run?.status}`)

    // Get the last run's data
    if (project.last_run && project.last_run.run_token) {
      const runToken = project.last_run.run_token
      console.log(`Fetching data for run ${runToken}...`)

      // Try to fetch CSV format first (preferred for data completeness)
      console.log(`Attempting CSV format...`)
      try {
        const csvResponse = await axios.get(`${BASE_URL}/runs/${runToken}/data`, {
          params: { api_key: API_KEY, format: 'csv' },
          timeout: 15000,
          headers: { 'Accept-Encoding': 'gzip' },
        })

        const csvText = csvResponse.data
        console.log(`CSV data fetched, parsing...`)

        // Parse CSV to records
        const records = parseCSV(csvText)
        const totalRecords = records.length

        console.log(`Parsed ${totalRecords} records from CSV`)

        // Return data in analytics format
        return {
          overview: {
            total_runs: 1,
            completed_runs: project.last_run.status === 'succeeded' || project.last_run.status === 'complete' ? 1 : 0,
            total_records_scraped: totalRecords,
            progress_percentage: (project.last_run.status === 'succeeded' || project.last_run.status === 'complete') ? 100 : 50,
          },
          performance: {
            items_per_minute: 0,
            estimated_total_items: totalRecords,
            average_run_duration_seconds: 0,
            current_items_count: totalRecords,
          },
          recovery: {
            in_recovery: false,
            status: 'normal',
            total_recovery_attempts: 0,
          },
          data_quality: {
            average_completion_percentage: 100,
            total_fields: records && records[0] ? Object.keys(records[0]).length : 0,
          },
          timeline: [],
          source: 'parsehub',
          raw_data: records,
          csv_data: csvText,
          run_token: runToken,
        }
      } catch (csvError) {
        console.log('CSV fetch failed, trying JSON...', csvError instanceof Error ? csvError.message : '')

        // Fallback to JSON format
        const jsonResponse = await axios.get(`${BASE_URL}/runs/${runToken}/data`, {
          params: { api_key: API_KEY },
          timeout: 15000,
        })

        const runData = jsonResponse.data
        console.log(`JSON data fetched`)

        // Extract records from the response
        let records = []
        let totalRecords = 0
        let dataKeys = Object.keys(runData).filter(k => !['offset', 'brand'].includes(k))

        // Look for array fields
        for (const key of dataKeys) {
          if (Array.isArray(runData[key])) {
            records = runData[key]
            totalRecords = records.length
            console.log(`Found ${totalRecords} records in field "${key}"`)
            break
          }
        }

        // Return data in analytics format
        return {
          overview: {
            total_runs: 1,
            completed_runs: project.last_run.status === 'succeeded' || project.last_run.status === 'complete' ? 1 : 0,
            total_records_scraped: totalRecords,
            progress_percentage: (project.last_run.status === 'succeeded' || project.last_run.status === 'complete') ? 100 : 50,
          },
          performance: {
            items_per_minute: 0,
            estimated_total_items: totalRecords,
            average_run_duration_seconds: 0,
            current_items_count: totalRecords,
          },
          recovery: {
            in_recovery: false,
            status: 'normal',
            total_recovery_attempts: 0,
          },
          data_quality: {
            average_completion_percentage: 100,
            total_fields: records && records[0] ? Object.keys(records[0]).length : 0,
          },
          timeline: [],
          source: 'parsehub',
          raw_data: records,
          run_token: runToken,
        }
      }
    }

    return null
  } catch (error) {
    console.error('Error fetching from ParseHub:', error instanceof Error ? error.message : error)
    return null
  }
}

// Fetch and store project data from ParseHub
async function fetchAndStoreProjectData(projectToken: string) {
  try {
    console.log(`[FETCH_STORE] Fetching and storing data for project ${projectToken}...`)

    // First try to get data from ParseHub
    let parseHubData = await fetchProjectDataFromParseHub(projectToken)

    if (!parseHubData) {
      console.log(`[FETCH_STORE] ParseHub API unavailable or returned null`)
      return null
    }

    console.log(`[FETCH_STORE] Got ParseHub data: ${parseHubData.overview?.total_records_scraped || 0} records`)

    if (parseHubData) {
      // ✅ Store to database SYNCHRONOUSLY and wait for completion
      const recordsToStore = parseHubData.raw_data || []
      console.log(`[FETCH_STORE] Storing ${recordsToStore.length} records to database...`)

      const storageResult = await storeAnalyticsDataToDB(
        projectToken,
        parseHubData.run_token || 'unknown',
        parseHubData,
        recordsToStore,
        parseHubData.csv_data
      )

      // ✅ Verify storage succeeded BEFORE returning response
      if (storageResult.success) {
        console.log(`✅ [FETCH_STORE] Data successfully stored to database for ${projectToken}`)
      } else {
        console.error(`❌ [FETCH_STORE] Database storage FAILED: ${storageResult.error}`)
        console.warn(`[FETCH_STORE] Returning data anyway, but data will be lost on next refresh!`)
      }

      // Return with raw_data included for display
      const response = {
        overview: parseHubData.overview,
        performance: parseHubData.performance,
        recovery: parseHubData.recovery,
        data_quality: parseHubData.data_quality,
        timeline: parseHubData.timeline,
        raw_data: parseHubData.raw_data,
        csv_data: parseHubData.csv_data,
        source: parseHubData.source,
        stored: storageResult.success,
        storage_message: storageResult.message,
      }

      console.log(`[FETCH_STORE] Returning response with ${recordsToStore.length} records`)
      return response
    }

    return null
  } catch (error) {
    console.error(`❌ [FETCH_STORE] Exception:`, error instanceof Error ? error.message : error)
    return null
  }
}

// Get analytics by executing Python script (DEPRECATED - using database instead)
// async function getAnalytics(token: string) {
/*
async function getAnalytics(token: string) {
  try {
    // Execute Python analytics script to get database info
    // process.cwd() is frontend directory, go up one level to reach project root
    const projectRoot = path.resolve(process.cwd(), '..')
    const backendDir = path.join(projectRoot, 'backend')
    const pythonScriptPath = path.join(backendDir, 'analytics.py')
    
    console.log('Project root:', projectRoot)
    console.log('Backend dir:', backendDir)
    console.log('Looking for analytics script at:', pythonScriptPath)
    
    if (fs.existsSync(pythonScriptPath)) {
      try {
        const pythonExe = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
        console.log('Python executable at:', pythonExe)
        
        if (fs.existsSync(pythonExe)) {
          const output = execSync(
            `"${pythonExe}" "${pythonScriptPath}" "${token}"`,
            { 
              cwd: backendDir,
              encoding: 'utf-8',
              maxBuffer: 10 * 1024 * 1024,
              stdio: ['pipe', 'pipe', 'pipe']
            }
          )
          
          console.log('Analytics output received, length:', output.length)
          
          // Parse JSON output
          try {
            const data = JSON.parse(output.trim())
            console.log('Parsed data structure:', Object.keys(data), 'has overview:', !!data.overview)
            
            // Check if this is already in the correct format (has overview/performance structure)
            if (data && data.overview && data.performance) {
              // Data is already in correct format, just return it
              console.log('Data already in correct format')
              return data
            }
            
            if (data && !data.error) {
              // Map the analytics data to the component's expected format
              console.log('Parsed analytics data successfully')
              const analyticsData = {
                overview: {
                  total_runs: data.overview?.total_runs || data.total_runs || 0,
                  completed_runs: data.overview?.completed_runs || data.completed_runs || 0,
                  total_records_scraped: data.overview?.total_records_scraped || data.total_records || 0,
                  progress_percentage: data.overview?.progress_percentage || data.progress_percentage || 0,
                },
                performance: {
                  items_per_minute: data.performance?.items_per_minute || data.items_per_minute || 0,
                  estimated_total_items: data.performance?.estimated_total_items || data.estimated_total_items || 0,
                  average_run_duration_seconds: data.performance?.average_run_duration_seconds || data.avg_duration || 0,
                  current_items_count: data.performance?.current_items_count || data.total_records || 0,
                },
                recovery: {
                  in_recovery: data.recovery?.in_recovery || false,
                  status: data.recovery?.status || 'normal',
                  total_recovery_attempts: data.recovery?.total_recovery_attempts || 0,
                },
                data_quality: {
                  average_completion_percentage: data.data_quality?.average_completion_percentage || 100,
                  total_fields: data.data_quality?.total_fields || 0,
                },
                timeline: data.timeline || [],
              }
              return analyticsData
            }
          } catch (parseError) {
            console.error('JSON parse error:', parseError)
            console.error('Output was:', output.substring(0, 500))
          }
        }
      } catch (execError) {
        console.error('Python script execution error:', execError)
        if (execError instanceof Error) {
          console.error('Error message:', execError.message)
        }
      }
    } else {
      console.log('Analytics script not found at:', pythonScriptPath)
    }
    
    // Fallback to monitoring results JSON
    const monitPath = path.join(projectRoot, 'monitoring_results.json')
    
    if (fs.existsSync(monitPath)) {
      console.log('Using fallback monitoring_results.json')
      const content = fs.readFileSync(monitPath, 'utf-8')
      const results = JSON.parse(content)

      if (results.project_data) {
        const projectData = results.project_data.find(
          (p: any) => p.token === token
        )

        if (projectData) {
          return {
            overview: {
              total_runs: 1,
              completed_runs: projectData.status === 'complete' ? 1 : 0,
              total_records_scraped: projectData.records || 0,
              progress_percentage: projectData.status === 'complete' ? 100 : 50,
            },
            performance: {
              items_per_minute: projectData.items_per_minute || 0,
              estimated_total_items: projectData.records || 0,
              average_run_duration_seconds: 0,
              current_items_count: projectData.records || 0,
            },
            recovery: {
              in_recovery: false,
              status: 'normal',
              total_recovery_attempts: 0,
            },
            data_quality: {
              average_completion_percentage: 100,
              total_fields: 0,
            },
            timeline: [],
          }
        }
      }
    }

    return null
  } catch (error) {
    console.error('Error getting analytics:', error)
    return null
  }
}
*/

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const token = searchParams.get('token') || process.env.PARSEHUB_API_KEY
    const force = searchParams.get('force') === 'true'

    console.log(`\n[API] GET /api/analytics token=${token}, force=${force}`)

    if (!token) {
      console.error(`[API] Error: No token provided`)
      return NextResponse.json(
        { error: 'Token is required' },
        { status: 400 }
      )
    }

    let analytics = null

    // ✅ ALWAYS check database first (fast response)
    console.log(`[API] Step 1: Checking database for ${token}...`)
    try {
      const dbResult = await Promise.race([
        getAnalyticsDataFromDB(token),
        new Promise<null>(resolve => setTimeout(() => resolve(null), 5000)) // 5 sec timeout
      ])

      analytics = dbResult
      if (analytics && analytics.raw_data && analytics.raw_data.length > 0) {
        console.log(`✅ [API] Step 1 SUCCESS: Found ${analytics.raw_data.length} records in database`)

        // Background update if force=true
        if (force) {
          console.log(`[API] Background: Queuing ParseHub update...`)
          // Don't await, let it run in background
          fetchAndStoreProjectData(token).catch(err => {
            console.warn(`[API] Background update failed (non-critical):`, err instanceof Error ? err.message : err)
          })
        }

        return NextResponse.json(analytics)
      } else {
        console.log(`[API] Step 1: No valid data in database, continuing...`)
      }
    } catch (err) {
      console.warn(`[API] Step 1 warning:`, err instanceof Error ? err.message : err)
    }

    // ✅ Second: Fetch from ParseHub with timeout (only if database is empty)
    if (!analytics) {
      console.log(`[API] Step 2: Fetching from ParseHub for token ${token} (with timeout)...`)
      try {
        // Use Promise.race with timeout for faster failure
        const result = await Promise.race([
          fetchAndStoreProjectData(token),
          new Promise<null>((_, reject) =>
            setTimeout(() => reject(new Error('ParseHub request timeout')), 12000) // 12 sec timeout
          )
        ])

        analytics = result
        if (analytics) {
          console.log(`✅ [API] Step 2 SUCCESS: Got data from ParseHub, stored=${(analytics as any).stored}`)
        }
      } catch (error) {
        console.warn(`[API] Step 2: ParseHub failed or timed out:`, error instanceof Error ? error.message : error)
        analytics = null
      }
    }

    // Return empty response with status message
    if (!analytics) {
      console.log(`[API] No data found, returning empty response`)
      return NextResponse.json({
        overview: {
          total_runs: 0,
          completed_runs: 0,
          total_records_scraped: 0,
          progress_percentage: 0,
        },
        performance: {
          items_per_minute: 0,
          estimated_total_items: 0,
          average_run_duration_seconds: 0,
          current_items_count: 0,
        },
        recovery: {
          in_recovery: false,
          status: 'no_data',
          total_recovery_attempts: 0,
        },
        data_quality: {
          average_completion_percentage: 0,
          total_fields: 0,
        },
        timeline: [],
        message: 'No data available. Try running the project first.',
      })
    }

    console.log(`[API] Returning response for ${token}`)
    return NextResponse.json(analytics)
  } catch (error) {
    console.error(`[API] Exception:`, error instanceof Error ? error.message : error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
