import { NextRequest, NextResponse } from 'next/server'
import * as fs from 'fs'
import * as path from 'path'

export const dynamic = 'force-dynamic'

function convertJsonToCsv(data: any): string {
  if (!Array.isArray(data) && typeof data === 'object') {
    // If data is a single object, wrap it
    data = [data]
  }

  if (!Array.isArray(data) || data.length === 0) {
    return ''
  }

  // Get all unique keys from all objects
  const keys = new Set<string>()
  data.forEach((item) => {
    if (typeof item === 'object' && item !== null) {
      Object.keys(item).forEach((key) => keys.add(key))
    }
  })

  const headers = Array.from(keys)
  const rows = data.map((item) =>
    headers
      .map((header) => {
        const value = item[header] ?? ''
        // Escape quotes and wrap in quotes if contains comma or quote
        const stringValue = String(value).replace(/"/g, '""')
        return stringValue.includes(',') || stringValue.includes('"')
          ? `"${stringValue}"`
          : stringValue
      })
      .join(',')
  )

  return [headers.join(','), ...rows].join('\n')
}

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const token = searchParams.get('token')
    const format = searchParams.get('format') || 'json'

    if (!token) {
      return NextResponse.json(
        { error: 'Token is required' },
        { status: 400 }
      )
    }

    // Construct path to data file
    const dataPath = path.join(process.cwd(), '..', `data_${token}.json`)

    if (!fs.existsSync(dataPath)) {
      return NextResponse.json(
        { error: 'Data file not found. Run the project first to generate data.' },
        { status: 404 }
      )
    }

    // Read the data file
    const fileContent = fs.readFileSync(dataPath, 'utf-8')
    const jsonData = JSON.parse(fileContent)

    if (format === 'csv') {
      // Convert JSON to CSV
      // Flatten if it contains a nested array (like { product: [...] })
      let dataToConvert = jsonData
      if (typeof jsonData === 'object' && !Array.isArray(jsonData)) {
        // Find the first array in the object
        for (const key in jsonData) {
          if (Array.isArray(jsonData[key])) {
            dataToConvert = jsonData[key]
            break
          }
        }
      }

      const csv = convertJsonToCsv(dataToConvert)
      return new NextResponse(csv, {
        headers: {
          'Content-Type': 'text/csv; charset=utf-8',
          'Content-Disposition': `attachment; filename="export_${token}.csv"`,
        },
      })
    } else {
      // Return as JSON
      return new NextResponse(JSON.stringify(jsonData, null, 2), {
        headers: {
          'Content-Type': 'application/json',
          'Content-Disposition': `attachment; filename="export_${token}.json"`,
        },
      })
    }
  } catch (error) {
    console.error('Error exporting data:', error)
    return NextResponse.json(
      { error: 'Failed to export data' },
      { status: 500 }
    )
  }
}
