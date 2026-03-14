import { getApiBaseUrl, getApiHeaders } from "@/lib/apiBase";
import { NextRequest, NextResponse } from 'next/server'

export async function GET(
  _request: NextRequest,
  { params }: { params: { token: string } }
) {
  try {
    const { token } = params
    const backendUrl = getApiBaseUrl();

    const response = await fetch(
      `${backendUrl}/api/projects/${token}/analytics`,
      {
        headers: {
          'Content-Type': 'application/json',
          ...getApiHeaders(),
        },
      }
    )

    if (!response.ok) {
      return NextResponse.json(
        { error: 'Failed to fetch analytics' },
        { status: response.status }
      )
    }

    const data = await response.json()
    return NextResponse.json(data)
  } catch (error) {
    console.error('Analytics API Error:', error)
    return NextResponse.json(
      { 
        error: 'Failed to fetch analytics',
        overview: {
          total_runs: 0,
          completed_runs: 0,
          total_records_scraped: 0,
          unique_records_estimate: 0,
          total_pages_analyzed: 0,
          progress_percentage: 0
        },
        performance: {
          items_per_minute: 0,
          estimated_completion_time: null,
          estimated_total_items: 0,
          average_run_duration_seconds: 0,
          current_items_count: 0
        },
        recovery: {
          in_recovery: false,
          status: 'error',
          total_recovery_attempts: 0
        },
        runs_history: [],
        data_quality: {
          average_completion_percentage: 0,
          total_fields: 0
        },
        timeline: []
      },
      { status: 200 }
    )
  }
}
