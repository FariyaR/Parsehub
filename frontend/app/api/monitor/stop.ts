import { getApiBaseUrl, getApiHeaders } from "@/lib/apiBase";
import { NextRequest, NextResponse } from 'next/server';

export async function POST(request: NextRequest) {
  try {
    const { sessionId, runToken } = await request.json();

    if (!sessionId && !runToken) {
      return NextResponse.json(
        { error: 'Missing required fields: sessionId or runToken' },
        { status: 400 }
      );
    }

    // Call Python backend API to stop monitoring
    const backendUrl = getApiBaseUrl();
    
    const response = await fetch(`${backendUrl}/api/monitor/stop`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getApiHeaders(),
      },
      body: JSON.stringify({
        session_id: sessionId,
        run_token: runToken,
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      console.error('Backend error:', error);
      return NextResponse.json(
        { error: 'Failed to stop monitoring' },
        { status: response.status }
      );
    }

    const data = await response.json();

    return NextResponse.json({
      success: true,
      stoppedAt: new Date().toISOString(),
      ...data,
    });
  } catch (error) {
    console.error('Error stopping monitoring:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}
