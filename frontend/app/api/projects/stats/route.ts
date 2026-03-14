import { NextRequest, NextResponse } from 'next/server'
import axios from 'axios'

export const dynamic = 'force-dynamic'

const API_KEY = process.env.PARSEHUB_API_KEY || ''
const BASE_URL = process.env.PARSEHUB_BASE_URL || 'https://www.parsehub.com/api/v2'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const token = searchParams.get('token')

    if (!token) {
      return NextResponse.json(
        { error: 'Token required' },
        { status: 400 }
      )
    }

    // Fetch project stats from ParseHub API
    const response = await axios.get(`${BASE_URL}/projects/${token}`, {
      params: { api_key: API_KEY },
      timeout: 10000
    })

    const project = response.data
    const lastRun = project.last_run || {}

    // Calculate detailed stats
    const stats = {
      project_token: token,
      project_title: project.title,
      total_runs: project.total_runs || 0,
      current_status: {
        status: lastRun.status || 'idle',
        pages_scraped: lastRun.pages_scraped || 0,
        data_count: lastRun.data_count || 0,
        start_time: lastRun.started_at,
        last_update: lastRun.fetch_start || lastRun.started_at,
        is_running: lastRun.status === 'running'
      },
      analytics: {
        total_records_all_time: calculateTotalRecords(project),
        pages_analyzed: lastRun.pages_scraped || 0,
        estimated_completion: estimateCompletion(lastRun),
        progress_percentage: calculateProgress(lastRun),
        items_per_minute: calculateItemsPerMinute(lastRun),
        data_quality: calculateDataQuality(lastRun)
      },
      recovery: {
        in_recovery: false,
        status: 'none',
        last_recovery_attempt: null,
        recovery_history: []
      }
    }

    return NextResponse.json(stats)
  } catch (error) {
    console.error('Stats endpoint error:', error)
    return NextResponse.json(
      { error: 'Failed to fetch project stats' },
      { status: 500 }
    )
  }
}

function calculateTotalRecords(project: any): number {
  // Sum records from all runs
  if (project.last_run && project.last_run.data_count) {
    return project.last_run.data_count
  }
  return 0
}

function calculateProgress(lastRun: any): number {
  if (!lastRun.data_count) return 0
  
  // Estimate based on pages and data count
  const estimatedItemsPerPage = 10
  const estimatedTotal = (lastRun.pages_scraped || 0) * estimatedItemsPerPage
  
  if (estimatedTotal === 0) return 0
  
  const progress = (lastRun.data_count / estimatedTotal) * 100
  return Math.min(Math.round(progress), 99)
}

function estimateCompletion(lastRun: any): string | null {
  if (!lastRun.data_count || !lastRun.started_at) return null
  
  try {
    const startTime = new Date(lastRun.started_at).getTime()
    const now = Date.now()
    const elapsedMs = now - startTime
    const elapsedMinutes = elapsedMs / 1000 / 60
    
    const itemsPerMinute = calculateItemsPerMinute(lastRun)
    if (itemsPerMinute === 0) return null
    
    // Estimate based on current rate
    const estimatedTotalTime = (lastRun.pages_scraped || 0) * 2  // rough estimate
    const remainingMinutes = estimatedTotalTime - elapsedMinutes
    
    if (remainingMinutes <= 0) return 'Soon'
    
    const completionTime = new Date(now + remainingMinutes * 60 * 1000)
    return completionTime.toLocaleTimeString()
  } catch (e) {
    return null
  }
}

function calculateItemsPerMinute(lastRun: any): number {
  if (!lastRun.data_count || !lastRun.started_at) return 0
  
  try {
    const startTime = new Date(lastRun.started_at).getTime()
    const now = Date.now()
    const elapsedMinutes = (now - startTime) / 1000 / 60
    
    if (elapsedMinutes === 0) return 0
    
    return Number((lastRun.data_count / elapsedMinutes).toFixed(2))
  } catch (e) {
    return 0
  }
}

function calculateDataQuality(lastRun: any): number {
  // Estimate data quality (0-100)
  // Based on data count relative to pages
  if (!lastRun.pages_scraped || !lastRun.data_count) return 0
  
  const expectedDataPerPage = 10
  const expectedTotal = lastRun.pages_scraped * expectedDataPerPage
  const quality = (lastRun.data_count / expectedTotal) * 100
  
  return Math.min(Math.round(quality), 100)
}
