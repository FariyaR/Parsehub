import { NextRequest, NextResponse } from 'next/server'
import { execSync } from 'child_process'
import path from 'path'

export const dynamic = 'force-dynamic'

export async function GET(request: NextRequest) {
  try {
    const sessionId = request.nextUrl.searchParams.get('session_id')

    if (!sessionId) {
      return NextResponse.json(
        { error: 'session_id parameter required' },
        { status: 400 }
      )
    }

    const projectRoot = path.resolve(process.cwd(), '..')
    const backendPath = path.join(projectRoot, 'backend')

    try {
      // Execute Python script to fetch progress
      const output = execSync(
        `python get_session_progress.py`,
        {
          cwd: backendPath,
          input: JSON.stringify({ session_id: parseInt(sessionId) }),
          encoding: 'utf-8',
          stdio: ['pipe', 'pipe', 'pipe'], // Capture stdout and stderr separately
          maxBuffer: 10 * 1024 * 1024, // 10MB buffer
        }
      )

      // Parse the output - should always be valid JSON now
      const progress = JSON.parse(output.trim())
      return NextResponse.json(progress)
    } catch (execError) {
      console.error('[API] Python script error:', execError)
      // If we can't execute the script, return a proper error response
      return NextResponse.json(
        { error: 'Failed to fetch session progress from backend', success: false },
        { status: 500 }
      )
    }
  } catch (error) {
    console.error('[API] Error fetching session progress:', error)
    return NextResponse.json(
      { error: 'Failed to fetch session progress' },
      { status: 500 }
    )
  }
}

