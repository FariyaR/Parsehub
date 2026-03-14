import { getApiBaseUrl, getApiHeaders } from "@/lib/apiBase";
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const projectId = searchParams.get('projectId');
    const sessionId = searchParams.get('sessionId');

    if (!projectId && !sessionId) {
      return NextResponse.json(
        { error: 'Missing required query parameter: projectId or sessionId' },
        { status: 400 }
      );
    }

    // Call Python backend API to get monitoring status
    const backendUrl = getApiBaseUrl();
    
    const params = new URLSearchParams();
    if (projectId) params.append('project_id', projectId);
    if (sessionId) params.append('session_id', sessionId);

    const response = await fetch(`${backendUrl}/api/monitor/status?${params}`, {
      method: 'GET',
      headers: getApiHeaders(),
    });

    if (!response.ok) {
      const error = await response.text();
      console.error('Backend error:', error);
      return NextResponse.json(
        { error: 'Failed to get monitoring status' },
        { status: response.status }
      );
    }

    const data = await response.json();

    return NextResponse.json({
      success: true,
      ...data,
    });
  } catch (error) {
    console.error('Error getting monitoring status:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}
