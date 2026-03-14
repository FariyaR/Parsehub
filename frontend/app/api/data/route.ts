import { NextRequest, NextResponse } from 'next/server'
import * as path from 'path'
import { execSync } from 'child_process'

export const dynamic = 'force-dynamic'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const token = searchParams.get('token')
    const projectId = searchParams.get('projectId')

    if (!token && !projectId) {
      return NextResponse.json(
        { error: 'Token or Project ID is required' },
        { status: 400 }
      )
    }

    const backendPath = path.resolve(process.cwd(), '..', 'backend')
    
    let command = ''
    let projectIdToUse = projectId

    if (!projectId && token) {
      // First get project ID from token
      command = `cd "${backendPath}" && python -c "
import sys
sys.path.insert(0, '.')
from database import ParseHubDatabase
db = ParseHubDatabase()
db.connect()
import sqlite3
db.connection.row_factory = sqlite3.Row
cursor = db.connection.cursor()
cursor.execute('SELECT id FROM projects WHERE token = ?', ('${token}',))
result = cursor.fetchone()
db.disconnect()
if result:
    print(result['id'])
else:
    print('0')
"`

      try {
        const projectIdResult = execSync(command).toString().trim()
        projectIdToUse = projectIdResult
        
        if (projectIdResult === '0') {
          return NextResponse.json(
            { error: 'Project not found' },
            { status: 404 }
          )
        }
      } catch (err) {
        console.error('Error getting project ID:', err)
        return NextResponse.json(
          { error: 'Failed to get project information' },
          { status: 500 }
        )
      }
    }

    // Now get the data from database
    command = `cd "${backendPath}" && python -c "
import sys
sys.path.insert(0, '.')
from database import ParseHubDatabase
import json
db = ParseHubDatabase()
db.connect()
import sqlite3
db.connection.row_factory = sqlite3.Row
cursor = db.connection.cursor()
cursor.execute('SELECT data FROM scraped_data WHERE project_id = ? ORDER BY created_at ASC LIMIT 1000', (${projectIdToUse},))
rows = cursor.fetchall()
db.disconnect()

data = []
for row in rows:
    try:
        data.append(json.loads(row['data']))
    except:
        pass

print(json.dumps(data))
"`

    try {
      const result = execSync(command, { encoding: 'utf-8' }).toString().trim()
      const data = result ? JSON.parse(result) : []

      return NextResponse.json({
        success: true,
        data,
        count: data.length,
        timestamp: new Date().toISOString(),
      })
    } catch (error) {
      console.error('Error executing command:', error)
      return NextResponse.json(
        { error: 'Failed to fetch data' },
        { status: 500 }
      )
    }
  } catch (error) {
    console.error('API Error:', error)
    return NextResponse.json(
      { error: 'Failed to fetch data' },
      { status: 500 }
    )
  }
}

