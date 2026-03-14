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

    // Call Python backend to get statistics
    const backendPath = path.resolve(process.cwd(), '..', 'backend')
    
    let command = ''
    if (projectId) {
      command = `cd "${backendPath}" && python -c "
import sys
sys.path.insert(0, '.')
from advanced_analytics import AdvancedAnalyticsService
service = AdvancedAnalyticsService()
stats = service.get_field_completion_report(${projectId})
import json
print(json.dumps(stats))
"`
    } else if (token) {
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
        const pid = parseInt(projectIdResult)
        
        if (pid === 0) {
          return NextResponse.json(
            { error: 'Project not found' },
            { status: 404 }
          )
        }
        
        // Now get statistics
        command = `cd "${backendPath}" && python -c "
import sys
sys.path.insert(0, '.')
from advanced_analytics import AdvancedAnalyticsService
service = AdvancedAnalyticsService()
stats = service.get_field_completion_report(${pid})
import json
print(json.dumps(stats))
"`
      } catch (err) {
        console.error('Error getting project ID:', err)
        return NextResponse.json(
          { error: 'Failed to get project information' },
          { status: 500 }
        )
      }
    }

    try {
      const result = execSync(command).toString().trim()
      const statistics = JSON.parse(result)
      
      return NextResponse.json({
        success: true,
        statistics,
        timestamp: new Date().toISOString(),
      })
    } catch (error) {
      console.error('Error executing command:', error)
      return NextResponse.json(
        { error: 'Failed to calculate statistics' },
        { status: 500 }
      )
    }
  } catch (error) {
    console.error('API Error:', error)
    return NextResponse.json(
      { error: 'Failed to fetch statistics' },
      { status: 500 }
    )
  }
}
