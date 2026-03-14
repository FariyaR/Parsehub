import { NextRequest, NextResponse } from 'next/server'
import * as fs from 'fs'
import * as path from 'path'

export const dynamic = 'force-dynamic'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const token = searchParams.get('token')

    if (!token) {
      return NextResponse.json(
        { error: 'Token is required' },
        { status: 400 }
      )
    }

    // Construct path to monitoring results file
    const monitringPath = path.join(process.cwd(), '..', 'monitoring_results.json')

    // Try to read monitoring results
    let logs: any[] = []

    try {
      if (fs.existsSync(monitringPath)) {
        const content = fs.readFileSync(monitringPath, 'utf-8')
        const results = JSON.parse(content)
        
        // Extract logs for this project
        if (results.project_data) {
          const projectData = results.project_data.find(
            (p: any) => p.token === token
          )
          
          if (projectData) {
            logs = [
              {
                timestamp: projectData.completed_at || new Date().toISOString(),
                status: projectData.status || 'unknown',
                pages: projectData.pages_scraped || 0,
                message: projectData.data_file
                  ? `Data saved to ${projectData.data_file}`
                  : projectData.note || 'Run completed',
              },
            ]
          }
        }
      }
    } catch (err) {
      console.error('Error reading monitoring results:', err)
    }

    // If no monitoring results, return empty logs
    if (logs.length === 0) {
      logs = [
        {
          timestamp: new Date().toISOString(),
          status: 'info',
          pages: 0,
          message: 'No monitoring logs available yet. Run the project to see logs.',
        },
      ]
    }

    return NextResponse.json({ logs })
  } catch (error) {
    console.error('Error fetching logs:', error)
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    )
  }
}
