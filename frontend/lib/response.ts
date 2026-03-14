export async function readResponseData<T>(response: Response): Promise<T> {
  const contentType = response.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }

  const text = await response.text();
  return {
    error: text,
    message: text,
    raw: text,
  } as T;
}

export function getResponseMessage(data: unknown, fallback: string): string {
  if (typeof data === "string" && data.trim()) {
    return data;
  }

  if (data && typeof data === "object") {
    const payload = data as Record<string, unknown>;

    if (typeof payload.message === "string" && payload.message) {
      return payload.message;
    }

    if (typeof payload.error === "string" && payload.error) {
      return payload.error;
    }

    if (typeof payload.details === "string" && payload.details) {
      return payload.details;
    }

    if (typeof payload.raw === "string" && payload.raw) {
      return payload.raw;
    }
  }

  return fallback;
}
