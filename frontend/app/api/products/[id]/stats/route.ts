import { getApiBaseUrl, getApiHeaders } from "@/lib/apiBase";
import { NextRequest, NextResponse } from "next/server";

export async function GET(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    const projectId = params.id;
    const backendUrl = getApiBaseUrl();
    
    // Call the backend API
    const response = await fetch(
      `${backendUrl}/api/products/${projectId}/stats`,
      {
        method: "GET",
        headers: getApiHeaders(),
      }
    );

    if (!response.ok) {
      console.error(
        `Backend error: ${response.status} ${response.statusText}`
      );
      return NextResponse.json(
        { error: `Failed to fetch product stats: ${response.statusText}` },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Error fetching product stats:", error);
    return NextResponse.json(
      { error: "Failed to fetch product stats" },
      { status: 500 }
    );
  }
}
