import { NextRequest, NextResponse } from "next/server";

const TIMEOUT_MS = 30_000;
const MAX_RETRIES = 2;

function getBackendBase(): string {
  const url = process.env.BACKEND_API_URL || process.env.BACKEND_URL || "";

  if (!url) {
    return "http://localhost:5000";
  }

  return url.replace(/\/$/, "");
}

function getApiKey(): string {
  return process.env.NEXT_PUBLIC_BACKEND_API_KEY || process.env.BACKEND_API_KEY || "";
}

function backendError(
  message: string,
  status: number,
  details?: string,
  backendStatus?: number,
): NextResponse {
  return NextResponse.json(
    {
      error: message,
      backend_status: backendStatus ?? null,
      details: details ?? null,
      backend_url: getBackendBase(),
    },
    { status },
  );
}

async function checkBackendHealth(
  backendBase: string,
  apiKey: string,
): Promise<{ ok: boolean; detail: string }> {
  try {
    const res = await fetch(`${backendBase}/api/health`, {
      headers: { Authorization: `Bearer ${apiKey}` },
      signal: AbortSignal.timeout(5_000),
    });

    if (res.ok) {
      return { ok: true, detail: "ok" };
    }

    return { ok: false, detail: `Flask /api/health returned ${res.status}` };
  } catch (e) {
    return {
      ok: false,
      detail: e instanceof Error ? e.message : String(e),
    };
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function proxyToBackend(
  req: NextRequest,
  backendPath: string,
  queryOverrides?: URLSearchParams,
): Promise<NextResponse> {
  const backendBase = getBackendBase();
  const apiKey = getApiKey();

  const qs = queryOverrides ?? req.nextUrl.searchParams;
  const queryString = qs.toString();
  const targetUrl = `${backendBase}${backendPath}${queryString ? `?${queryString}` : ""}`;
  const method = req.method.toUpperCase();

  console.log(`[proxy] ${method} ${req.nextUrl.pathname} -> ${targetUrl}`);

  let body: string | undefined;
  if (!["GET", "HEAD", "DELETE"].includes(method)) {
    try {
      body = await req.text();
    } catch {
      body = undefined;
    }
  }

  const outgoingHeaders: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...(apiKey ? { Authorization: `Bearer ${apiKey}`, "x-api-key": apiKey } : {}),
  };

  const isIdempotent = ["GET", "HEAD", "DELETE"].includes(method);
  const maxAttempts = isIdempotent ? MAX_RETRIES + 1 : 1;

  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    if (attempt > 1) {
      const delayMs = 500 * 2 ** (attempt - 2);
      console.log(`[proxy] Retry ${attempt - 1}/${MAX_RETRIES} in ${delayMs}ms for ${targetUrl}`);
      await sleep(delayMs);
    }

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

      const contentType = backendResponse.headers.get("content-type") ?? "";
      let responseData: unknown;
      if (contentType.includes("application/json")) {
        responseData = await backendResponse.json();
      } else {
        const raw = await backendResponse.text();
        responseData = { raw };
      }

      if (!backendResponse.ok) {
        const errMsg =
          (responseData as Record<string, string>)?.error ||
          `Backend returned ${backendResponse.status}`;

        console.error(`[proxy] ${backendResponse.status} from ${targetUrl}: ${errMsg}`);

        if (backendResponse.status === 503) {
          return backendError(
            "Database is not ready. The backend is booting - please retry in a moment.",
            503,
            errMsg,
            503,
          );
        }

        if (backendResponse.status >= 500 && attempt < maxAttempts) {
          lastError = new Error(errMsg);
          continue;
        }

        return backendError(errMsg, backendResponse.status, undefined, backendResponse.status);
      }

      return NextResponse.json(responseData, { status: backendResponse.status });
    } catch (err: unknown) {
      clearTimeout(timer);
      lastError = err;

      if (err instanceof Error && (err.name === "AbortError" || err.name === "TimeoutError")) {
        console.error(`[proxy] Timeout after ${TIMEOUT_MS}ms for ${targetUrl}`);
        if (attempt < maxAttempts) {
          continue;
        }
        return backendError(
          "Backend request timed out. The server may be overloaded - please retry.",
          504,
        );
      }

      if (attempt < maxAttempts) {
        continue;
      }

      const health = await checkBackendHealth(backendBase, apiKey);
      const detail = err instanceof Error ? err.message : String(err);

      console.error(`[proxy] Network error for ${targetUrl}:`, detail);
      console.error(`[proxy] Flask health: ${health.detail}`);

      if (!health.ok) {
        return backendError(
          "Flask backend is unreachable. It may still be booting on Railway.",
          502,
          health.detail,
        );
      }

      return backendError("Backend request failed. Please try again.", 502, detail);
    }
  }

  const detail = lastError instanceof Error ? lastError.message : String(lastError);
  return backendError("All retry attempts failed.", 502, detail);
}
