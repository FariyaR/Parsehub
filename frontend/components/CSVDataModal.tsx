"use client";
import apiClient from "@/lib/apiClient";
import { getApiHeaders } from "@/lib/apiBase";
import { getResponseMessage, readResponseData } from "@/lib/response";

import { useState, useEffect } from "react";
import { Download, Eye, FileJson, Loader2, FileText } from "lucide-react";
import Modal from "./Modal";

interface CSVDataModalProps {
  token: string;
  title: string;
  isOpen: boolean;
  onClose: () => void;
}

export default function CSVDataModal({
  token,
  title,
  isOpen,
  onClose,
}: CSVDataModalProps) {
  const [csvData, setCSVData] = useState<string>("");
  const [jsonData, setJsonData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"table" | "raw">("table");

  useEffect(() => {
    if (isOpen && token) {
      fetchCSVData();
    }
  }, [isOpen, token]);

  const fetchCSVData = async () => {
    setLoading(true);
    setError(null);
    try {
      // Add 30 second timeout
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000);

      try {
        const response = await fetch(
          `/api/analytics?token=${token}&force=true`,
          {
            signal: controller.signal,
            headers: getApiHeaders(),
          },
        );
        const result = await readResponseData<Record<string, unknown>>(response);

        if (!response.ok) {
          throw new Error(
            getResponseMessage(
              result,
              `API error: ${response.status} ${response.statusText}`,
            ),
          );
        }

        // Extract CSV data
        if (typeof result.csv_data === "string" && result.csv_data) {
          setCSVData(result.csv_data);
          // Parse CSV to JSON for table view
          parseCSVToJSON(result.csv_data);
        } else {
          setError(
            "No CSV data found for this project. Try running the project first.",
          );
        }
      } finally {
        clearTimeout(timeoutId);
      }
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        setError(
          "Request timed out (took longer than 30 seconds). The server might be busy.",
        );
      } else {
        setError(err instanceof Error ? err.message : "Unknown error occurred");
      }
    } finally {
      setLoading(false);
    }
  };

  const parseCSVToJSON = (csv: string) => {
    const lines = csv.trim().split("\n");
    if (lines.length < 2) {
      setJsonData([]);
      return;
    }

    const headers = lines[0]
      .split(",")
      .map((h) => h.trim().replace(/^"|"$/g, ""));
    const data = lines.slice(1).map((line) => {
      const values = line.split(",").map((v) => v.trim().replace(/^"|"$/g, ""));
      const obj: any = {};
      headers.forEach((header, index) => {
        obj[header] = values[index] || "";
      });
      return obj;
    });
    setJsonData(data);
  };

  const downloadCSV = () => {
    if (!csvData) return;
    const element = document.createElement("a");
    element.setAttribute(
      "href",
      "data:text/csv;charset=utf-8," + encodeURIComponent(csvData),
    );
    element.setAttribute("download", `${token}-data.csv`);
    element.style.display = "none";
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  const downloadJSON = () => {
    if (jsonData.length === 0) return;
    const dataStr = JSON.stringify(jsonData, null, 2);
    const element = document.createElement("a");
    element.setAttribute(
      "href",
      "data:application/json;charset=utf-8," + encodeURIComponent(dataStr),
    );
    element.setAttribute("download", `${token}-data.json`);
    element.style.display = "none";
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  if (!isOpen) return null;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`CSV Data - ${title}`}
      size="xlarge"
    >
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="relative">
            <Loader2 className="w-12 h-12 text-blue-500 animate-spin" />
            <div className="absolute inset-0 blur-xl bg-blue-500/20 animate-pulse"></div>
          </div>
        </div>
      ) : error ? (
        <div className="bg-red-900/20 border border-red-500/50 text-red-200 px-6 py-4 rounded-lg backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse"></div>
            <p className="font-medium">Error: {error}</p>
          </div>
        </div>
      ) : csvData ? (
        <div className="space-y-6">
          {/* Header with download buttons */}
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-2 bg-slate-800/50 backdrop-blur-sm rounded-lg p-1 border border-slate-700/50">
              <button
                onClick={() => setViewMode("table")}
                className={`flex items-center gap-2 px-4 py-2 rounded-md font-medium transition-all duration-200 ${
                  viewMode === "table"
                    ? "bg-gradient-to-r from-blue-600 to-blue-500 text-white shadow-lg shadow-blue-500/20"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/50"
                }`}
              >
                <Eye className="w-4 h-4" />
                Table View
              </button>
              <button
                onClick={() => setViewMode("raw")}
                className={`flex items-center gap-2 px-4 py-2 rounded-md font-medium transition-all duration-200 ${
                  viewMode === "raw"
                    ? "bg-gradient-to-r from-blue-600 to-blue-500 text-white shadow-lg shadow-blue-500/20"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-700/50"
                }`}
              >
                <FileJson className="w-4 h-4" />
                Raw CSV
              </button>
            </div>
            <div className="flex gap-2">
              <button
                onClick={downloadCSV}
                className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-emerald-600 to-emerald-500 hover:from-emerald-500 hover:to-emerald-400 text-white rounded-lg font-medium transition-all duration-200 shadow-lg shadow-emerald-500/20 hover:shadow-emerald-400/30 hover:scale-105"
              >
                <Download className="w-4 h-4" />
                Download CSV
              </button>
              <button
                onClick={downloadJSON}
                className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-purple-600 to-purple-500 hover:from-purple-500 hover:to-purple-400 text-white rounded-lg font-medium transition-all duration-200 shadow-lg shadow-purple-500/20 hover:shadow-purple-400/30 hover:scale-105"
              >
                <Download className="w-4 h-4" />
                Download JSON
              </button>
            </div>
          </div>

          {/* Content */}
          {viewMode === "table" ? (
            // Table view
            <div className="overflow-x-auto border border-slate-700/50 rounded-lg shadow-xl custom-scrollbar">
              {jsonData.length > 0 ? (
                <table className="w-full border-collapse">
                  <thead className="sticky top-0 z-10">
                    <tr className="bg-gradient-to-r from-slate-800 to-slate-800/80 border-b border-slate-700">
                      {Object.keys(jsonData[0]).map((key) => (
                        <th
                          key={key}
                          className="px-4 py-3 text-left text-sm font-semibold text-slate-200 border-r border-slate-700/50 last:border-r-0 backdrop-blur-sm"
                        >
                          {key}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {jsonData.map((row, idx) => (
                      <tr
                        key={idx}
                        className={`border-b border-slate-800 transition-colors duration-150 ${
                          idx % 2 === 0 ? "bg-slate-900/50" : "bg-slate-900/30"
                        } hover:bg-slate-800/50`}
                      >
                        {Object.values(row).map((value: any, cellIdx) => (
                          <td
                            key={cellIdx}
                            className="px-4 py-3 text-sm text-slate-300 border-r border-slate-800/50 last:border-r-0 break-words"
                          >
                            {typeof value === "string" ? (
                              value.length > 100 ? (
                                <span title={value}>
                                  {value.substring(0, 100)}
                                  <span className="text-slate-500">...</span>
                                </span>
                              ) : (
                                value
                              )
                            ) : (
                              <span className="text-blue-400">
                                {String(value)}
                              </span>
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="text-center py-8 text-slate-500">
                  No data rows found
                </div>
              )}
            </div>
          ) : (
            // Raw CSV view
            <div className="border border-slate-700/50 rounded-lg p-4 bg-slate-900/50 backdrop-blur-sm shadow-xl">
              <pre className="text-xs font-mono overflow-auto max-h-96 text-slate-300 whitespace-pre-wrap break-words custom-scrollbar">
                {csvData}
              </pre>
            </div>
          )}

          {/* Stats */}
          <div className="relative overflow-hidden rounded-lg p-4 backdrop-blur-sm border border-slate-700/50 bg-gradient-to-br from-slate-800/50 to-slate-900/50">
            <div className="absolute inset-0 bg-gradient-to-r from-blue-500/5 via-transparent to-purple-500/5"></div>
            <div className="relative text-sm space-y-1">
              <p className="text-slate-300">
                <span className="font-semibold text-blue-400">
                  Total Records:
                </span>{" "}
                <span className="text-slate-100 font-mono">
                  {jsonData.length.toLocaleString()}
                </span>
              </p>
              {jsonData.length > 0 && (
                <p className="text-slate-300">
                  <span className="font-semibold text-emerald-400">
                    Columns:
                  </span>{" "}
                  <span className="text-slate-100 font-mono">
                    {Object.keys(jsonData[0]).length}
                  </span>
                </p>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="text-center py-12">
          <div className="relative inline-block">
            <FileText className="w-16 h-16 mx-auto mb-3 text-slate-600" />
            <div className="absolute inset-0 blur-2xl bg-slate-500/10"></div>
          </div>
          <p className="text-slate-400 text-lg">No data available</p>
        </div>
      )}
    </Modal>
  );
}
