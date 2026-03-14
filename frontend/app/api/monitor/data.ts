import { getApiBaseUrl, getApiHeaders } from "@/lib/apiBase";
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const sessionId = searchParams.get('sessionId');
    const limit = searchParams.get('limit') || '100';
    const offset = searchParams.get('offset') || '0';

    if (!sessionId) {
      return NextResponse.json(
        { error: 'Missing required query parameter: sessionId' },
        { status: 400 }
      );
    }

    // Call Python backend API to get session records
    const backendUrl = getApiBaseUrl();
    
    const params = new URLSearchParams();
    params.append('session_id', sessionId);
    params.append('limit', limit);
    params.append('offset', offset);

    const response = await fetch(`${backendUrl}/api/monitor/data?${params}`, {
      method: 'GET',
      headers: getApiHeaders(),
    });

    if (!response.ok) {
      const error = await response.text();
      console.error('Backend error:', error);
      return NextResponse.json(
        { error: 'Failed to get monitoring data' },
        { status: response.status }
      );
    }

    const data = await response.json();

    return NextResponse.json({
      success: true,
      records: data.records,
      total: data.total,
      limit: parseInt(limit),
      offset: parseInt(offset),
    });
  } catch (error) {
    console.error('Error getting monitoring data:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}
