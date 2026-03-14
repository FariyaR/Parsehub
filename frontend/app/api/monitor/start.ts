import { getApiBaseUrl, getApiHeaders } from "@/lib/apiBase";
import { NextRequest, NextResponse } from 'next/server';

export async function POST(request: NextRequest) {
  try {
    const { projectToken, runToken, pages } = await request.json();

    if (!projectToken || !runToken || !pages) {
      return NextResponse.json(
        { error: 'Missing required fields: projectToken, runToken, pages' },
        { status: 400 }
      );
    }

    // Call Python backend API to start monitoring
    const backendUrl = getApiBaseUrl();
    
    const response = await fetch(`${backendUrl}/api/monitor/start`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getApiHeaders(),
      },
      body: JSON.stringify({
        run_token: runToken,
        pages: pages,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      console.error('Backend error:', error);
      return NextResponse.json(
        { error: 'Failed to start monitoring on backend' },
        { status: response.status }
      );
    }

    const data = await response.json();

    return NextResponse.json({
      success: true,
      sessionId: data.session_id,
      runToken: runToken,
      startedAt: new Date().toISOString(),
    });
  } catch (error) {
    console.error('Error starting monitoring:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}
