import { getApiBaseUrl, getApiHeaders } from "@/lib/apiBase";
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/**
 * GET /api/import-history
 * Fetches Excel import batch history
 * Proxies to Flask backend: GET /api/metadata/import-history
 */
export async function GET(request: NextRequest) {
  try {
    // Extract query parameters
    const searchParams = request.nextUrl.searchParams;
    const limit = searchParams.get('limit') || '50';
    const offset = searchParams.get('offset') || '0';

    // Build backend URL
    const params = new URLSearchParams();
    params.append('limit', limit);
    params.append('offset', offset);

    const backendBase = getApiBaseUrl();
    const backendUrl = `${backendBase}/api/metadata/import-history?${params.toString()}`;

    console.log(`[API] GET /api/import-history - Proxying to: ${backendUrl}`);

    // Call Flask backend
    const response = await fetch(backendUrl, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        ...getApiHeaders(),
      },
    });

    const data = await response.json();

    if (!response.ok) {
      console.error(`[API] Backend returned ${response.status}:`, data);
      return NextResponse.json(
        { error: data.error || 'Failed to fetch import history' },
        { status: response.status }
      );
    }

    console.log(`[API] Successfully fetched import history - Count: ${data.count}`);
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error('[API] Error fetching import history:', error);
    return NextResponse.json(
      { error: 'Failed to fetch import history', details: String(error) },
      { status: 500 }
    );
  }
}
