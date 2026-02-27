/**
 * _proxy.ts — Central server-side proxy utility
 *
 * All Next.js API route handlers call `proxyToBackend()` from here.
 * Browser never touches the Flask domain — only hits same-origin /api/*.
 *
 * BACKEND_API_URL is a server-only env var (no NEXT_PUBLIC prefix).
 *
 * Error surface:
 *   502 - Flask is down, ECONNREFUSED, or network unreachable
 *   503 - Flask booted but DB is not ready (returned by /api/health/db)
 *   504 - Request timed out
 *   5xx - Flask returned an error itself (forwarded as-is)
 *
 * All errors return a clean JSON { error, backend_status, details } payload
 * so the frontend can display a meaningful message.
 */

import { NextRequest, NextResponse } from 'next/server';

const TIMEOUT_MS = 30_000;

// ── Backend URL resolution ─────────────────────────────────────────────────
function getBackendUrl(): string {
    const url =
        process.env.BACKEND_API_URL ||
        process.env.BACKEND_URL ||
        'http://localhost:5000';

    if (process.env.NODE_ENV === 'production') {
        if (!process.env.BACKEND_API_URL && !process.env.BACKEND_URL) {
            throw new Error(
                '[proxy] BACKEND_API_URL is not set. ' +
                'Add it to Railway → frontend service → Variables.'
            );
        }
        if (
            url.includes('localhost') ||
            url.includes('127.0.0.1') ||
            url.includes('0.0.0.0')
        ) {
            throw new Error(
                `[proxy] BACKEND_API_URL resolves to a local address in production: ${url}`
            );
        }
    }

    return url.replace(/\/$/, '');
}

const BACKEND_BASE = getBackendUrl();
const BACKEND_API_KEY = process.env.BACKEND_API_KEY || 't_hmXetfMCq3';

// ── Helper: build error payload ────────────────────────────────────────────
function backendError(
    message: string,
    status: number,
    details?: string,
    backendStatus?: number
): NextResponse {
    return NextResponse.json(
        {
            error: message,
            backend_status: backendStatus ?? null,
            details: details ?? null,
            backend_url: BACKEND_BASE,   // helps debug Railway config issues
        },
        { status }
    );
}

// ── Helper: check Flask health before surfacing opaque 502s ───────────────
async function checkBackendHealth(): Promise<{ ok: boolean; detail: string }> {
    try {
        const res = await fetch(`${BACKEND_BASE}/api/health`, {
            headers: { Authorization: `Bearer ${BACKEND_API_KEY}` },
            signal: AbortSignal.timeout(5_000),
        });
        if (res.ok) return { ok: true, detail: 'ok' };
        return { ok: false, detail: `Flask /api/health returned ${res.status}` };
    } catch (e) {
        return {
            ok: false,
            detail: e instanceof Error ? e.message : String(e),
        };
    }
}

// ── Core proxy function ────────────────────────────────────────────────────
/**
 * Forward a Next.js route request to the Flask backend.
 *
 * @param request        - Incoming NextRequest
 * @param backendPath    - Path on Flask, e.g. "/api/projects"
 * @param queryOverrides - Optional URLSearchParams (replaces request search params)
 */
export async function proxyToBackend(
    request: NextRequest,
    backendPath: string,
    queryOverrides?: URLSearchParams
): Promise<NextResponse> {
    const qs = queryOverrides ?? request.nextUrl.searchParams;
    const queryString = qs.toString();
    const targetUrl = `${BACKEND_BASE}${backendPath}${queryString ? `?${queryString}` : ''}`;

    const method = request.method.toUpperCase();
    console.log(`[proxy] ${method} ${request.nextUrl.pathname} → ${targetUrl}`);

    // Forward body for mutating methods
    let body: string | undefined;
    if (!['GET', 'HEAD', 'DELETE'].includes(method)) {
        try {
            body = await request.text();
        } catch {
            body = undefined;
        }
    }

    const outgoingHeaders: Record<string, string> = {
        'Authorization': `Bearer ${BACKEND_API_KEY}`,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    };

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

    try {
        const backendResponse = await fetch(targetUrl, {
            method,
            headers: outgoingHeaders,
            body: body || undefined,
            signal: controller.signal,
        });

        clearTimeout(timer);

        // Parse response body
        const contentType = backendResponse.headers.get('content-type') ?? '';
        let responseData: unknown;
        if (contentType.includes('application/json')) {
            responseData = await backendResponse.json();
        } else {
            const raw = await backendResponse.text();
            responseData = { raw };
        }

        if (!backendResponse.ok) {
            const errMsg =
                (responseData as Record<string, string>)?.error ??
                `Backend returned ${backendResponse.status}`;

            console.error(`[proxy] ${backendResponse.status} from ${targetUrl}: ${errMsg}`);

            // Special: 503 means Flask is up but DB is not ready
            if (backendResponse.status === 503) {
                return backendError(
                    'Database is not ready. The backend is booting — please retry in a moment.',
                    503,
                    errMsg,
                    503
                );
            }

            return backendError(errMsg, backendResponse.status, undefined, backendResponse.status);
        }

        return NextResponse.json(responseData, { status: backendResponse.status });

    } catch (err: unknown) {
        clearTimeout(timer);

        // Timeout
        if (err instanceof Error && (err.name === 'AbortError' || err.name === 'TimeoutError')) {
            console.error(`[proxy] Timeout after ${TIMEOUT_MS}ms for ${targetUrl}`);
            return backendError(
                'Backend request timed out. The server may be overloaded — please retry.',
                504
            );
        }

        // Network error (ECONNREFUSED, ENOTFOUND, etc.)
        // Query /api/health to distinguish "Flask is down" vs "transient error"
        const health = await checkBackendHealth();
        const detail = err instanceof Error ? err.message : String(err);

        console.error(`[proxy] Network error for ${targetUrl}:`, detail);
        console.error(`[proxy] Flask health check: ${health.detail}`);

        if (!health.ok) {
            return backendError(
                'Flask backend is unreachable. It may still be booting on Railway.',
                502,
                health.detail
            );
        }

        // Flask is alive but this specific request failed
        return backendError(
            'Backend request failed. Please try again.',
            502,
            detail
        );
    }
}
