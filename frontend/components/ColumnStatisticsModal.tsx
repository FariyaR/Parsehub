"use client";
import apiClient from "@/lib/apiClient";
import { getApiHeaders } from "@/lib/apiBase";
import { getResponseMessage, readResponseData } from "@/lib/response";

import { useState, useEffect } from "react";
import { Download, BarChart3 } from "lucide-react";
import Modal from "./Modal";

interface ColumnStatisticsModalProps {
  token: string;
  title: string;
  isOpen: boolean;
  onClose: () => void;
}

interface ColumnStats {
  [columnName: string]: {
    [value: string]: number;
  };
}

export default function ColumnStatisticsModal({
  token,
  title,
  isOpen,
  onClose,
}: ColumnStatisticsModalProps) {
  const [stats, setStats] = useState<ColumnStats>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedColumn, setSelectedColumn] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && token) {
      fetchAndAnalyzeCSV();
    }
  }, [isOpen, token]);

  const fetchAndAnalyzeCSV = async () => {
    setLoading(true);
    setError(null);
    try {
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
            getResponseMessage(result, `API error: ${response.status}`),
          );
        }

        if (typeof result.csv_data !== "string" || !result.csv_data) {
          setError(
            "No CSV data found for this project. Try running the project first.",
          );
          setStats({});
          return;
        }

        // Parse CSV and calculate statistics
        const columnStats = parseCSVAndCalculateStats(result.csv_data);
        setStats(columnStats);

        // Auto-select first column
        const columns = Object.keys(columnStats);
        if (columns.length > 0) {
          setSelectedColumn(columns[0]);
        }
      } finally {
        clearTimeout(timeoutId);
      }
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        setError("Request timed out. The server might be busy.");
      } else {
        setError(err instanceof Error ? err.message : "Failed to fetch data");
      }
      setStats({});
    } finally {
      setLoading(false);
    }
  };

  const parseCSVAndCalculateStats = (csv: string): ColumnStats => {
    const lines = csv.trim().split("\n");
    if (lines.length < 2) return {};

    const headers = lines[0]
      .split(",")
      .map((h) => h.trim().replace(/^"|"$/g, ""));
    const columnStats: ColumnStats = {};

    // Initialize column stats
    headers.forEach((header) => {
      columnStats[header] = {};
    });

    // Process each row and count values
    for (let i = 1; i < lines.length; i++) {
      if (!lines[i].trim()) continue;

      const values: string[] = [];
      let current = "";
      let inQuotes = false;

      for (let j = 0; j < lines[i].length; j++) {
        const char = lines[i][j];
        if (char === '"') {
          inQuotes = !inQuotes;
        } else if (char === "," && !inQuotes) {
          values.push(current.trim().replace(/^"|"$/g, ""));
          current = "";
        } else {
          current += char;
        }
      }
      values.push(current.trim().replace(/^"|"$/g, ""));

      // Count values for each column
      headers.forEach((header, index) => {
        const value = values[index] || "N/A";
        const displayValue =
          value.length > 50 ? value.substring(0, 50) + "..." : value;
        columnStats[header][displayValue] =
          (columnStats[header][displayValue] || 0) + 1;
      });
    }

    // Sort values by count (descending)
    Object.keys(columnStats).forEach((column) => {
      const sorted = Object.entries(columnStats[column])
        .sort((a, b) => b[1] - a[1])
        .reduce(
          (acc, [value, count]) => {
            acc[value] = count;
            return acc;
          },
          {} as Record<string, number>,
        );
      columnStats[column] = sorted;
    });

    return columnStats;
  };

  const downloadStatisticsAsJSON = () => {
    const dataStr = JSON.stringify(stats, null, 2);
    const element = document.createElement("a");
    element.setAttribute(
      "href",
      "data:application/json;charset=utf-8," + encodeURIComponent(dataStr),
    );
    element.setAttribute("download", `${token}-statistics.json`);
    element.style.display = "none";
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  const getTotalRecordCount = (): number => {
    if (Object.keys(stats).length === 0) return 0;
    const firstColumn = Object.keys(stats)[0];
    return Object.values(stats[firstColumn]).reduce((a, b) => a + b, 0);
  };

  if (!isOpen) return null;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Data Statistics - ${title}`}
      size="xlarge"
    >
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="inline-flex flex-col items-center gap-4">
            <div className="relative">
              <div className="animate-spin rounded-full h-12 w-12 border-4 border-slate-700 border-t-blue-500"></div>
              <div className="absolute inset-0 rounded-full bg-blue-500/20 animate-pulse"></div>
            </div>
            <p className="text-slate-400 font-medium">Loading statistics...</p>
          </div>
        </div>
      ) : error ? (
        <div className="bg-red-900/30 border border-red-700/50 text-red-300 px-6 py-4 rounded-xl">
          <p className="font-semibold text-red-400">Error</p>
          <p className="text-sm mt-1">{error}</p>
        </div>
      ) : Object.keys(stats).length === 0 ? (
        <div className="text-center py-16">
          <BarChart3 className="w-16 h-16 text-slate-600 mx-auto mb-4" />
          <p className="text-slate-400 text-lg font-medium">
            No statistical data available
          </p>
          <p className="text-slate-500 text-sm mt-2">
            Try running the project first
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Header */}
          <div className="flex items-center justify-between gap-4 flex-wrap pb-4 border-b border-slate-700/50">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-blue-600/20 rounded-lg border border-blue-600/30">
                <BarChart3 className="w-5 h-5 text-blue-400" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-slate-300">
                    Total Records:
                  </span>
                  <span className="font-bold text-2xl bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">
                    {getTotalRecordCount().toLocaleString()}
                  </span>
                </div>
                <span className="text-slate-500 text-sm">
                  {Object.keys(stats).length} columns available
                </span>
              </div>
            </div>
            <button
              onClick={downloadStatisticsAsJSON}
              className="flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-emerald-600 to-emerald-700 hover:from-emerald-700 hover:to-emerald-800 text-white rounded-xl font-semibold transition-all duration-200 shadow-lg hover:shadow-emerald-500/25"
            >
              <Download className="w-4 h-4" />
              Download Stats
            </button>
          </div>

          {/* Column Selector */}
          <div className="bg-slate-800/50 border border-slate-700/50 rounded-xl p-5">
            <label className="text-sm font-semibold text-slate-300 block mb-3">
              Select Column to View:
            </label>
            <select
              value={selectedColumn || ""}
              onChange={(e) => setSelectedColumn(e.target.value)}
              className="w-full px-4 py-3 bg-slate-900/50 border border-slate-600 rounded-xl text-slate-200 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
            >
              <option value="" className="bg-slate-800">
                -- Choose a column --
              </option>
              {Object.keys(stats).map((column) => (
                <option key={column} value={column} className="bg-slate-800">
                  {column} ({Object.keys(stats[column]).length} unique values)
                </option>
              ))}
            </select>
          </div>

          {/* Statistics Table */}
          {selectedColumn && stats[selectedColumn] ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-lg text-slate-200">
                  {selectedColumn}
                </h3>
                <span className="text-sm text-slate-400 bg-slate-800/50 px-3 py-1 rounded-lg border border-slate-700/50">
                  {Object.keys(stats[selectedColumn]).length} unique values
                </span>
              </div>

              <div className="overflow-hidden border border-slate-700/50 rounded-xl">
                <div className="overflow-x-auto max-h-96 overflow-y-auto custom-scrollbar">
                  <table className="w-full border-collapse">
                    <thead>
                      <tr className="bg-gradient-to-r from-slate-800 to-slate-800/80 border-b border-slate-700/50 sticky top-0 z-10">
                        <th className="px-6 py-4 text-left text-xs font-semibold text-slate-300 uppercase tracking-wider border-r border-slate-700/30">
                          Value
                        </th>
                        <th className="px-6 py-4 text-center text-xs font-semibold text-slate-300 uppercase tracking-wider min-w-24">
                          Count
                        </th>
                        <th className="px-6 py-4 text-right text-xs font-semibold text-slate-300 uppercase tracking-wider min-w-48">
                          Percentage
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-700/30">
                      {Object.entries(stats[selectedColumn]).map(
                        ([value, count], index) => {
                          const total = Object.values(
                            stats[selectedColumn],
                          ).reduce((a, b) => a + b, 0);
                          const percentage = ((count / total) * 100).toFixed(2);

                          return (
                            <tr
                              key={index}
                              className={`hover:bg-slate-700/30 transition-colors ${
                                index % 2 === 0
                                  ? "bg-slate-800/30"
                                  : "bg-slate-800/50"
                              }`}
                            >
                              <td className="px-6 py-3 text-sm text-slate-300 font-mono break-words border-r border-slate-700/30">
                                {value}
                              </td>
                              <td className="px-6 py-3 text-center">
                                <span className="inline-flex items-center px-3 py-1 rounded-lg bg-blue-500/20 text-blue-400 font-semibold text-sm border border-blue-500/30">
                                  {count.toLocaleString()}
                                </span>
                              </td>
                              <td className="px-6 py-3">
                                <div className="flex items-center justify-end gap-3">
                                  <div className="w-32 bg-slate-700/50 rounded-full h-2.5 border border-slate-600/50">
                                    <div
                                      className="bg-gradient-to-r from-blue-500 to-cyan-500 h-2.5 rounded-full transition-all shadow-lg shadow-blue-500/25"
                                      style={{ width: `${percentage}%` }}
                                    ></div>
                                  </div>
                                  <span className="text-slate-300 font-semibold text-sm min-w-16 text-right">
                                    {percentage}%
                                  </span>
                                </div>
                              </td>
                            </tr>
                          );
                        },
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Summary Stats */}
              <div className="grid grid-cols-3 gap-4 mt-6">
                <div className="bg-gradient-to-br from-blue-900/30 to-blue-800/20 border border-blue-700/30 rounded-xl p-4 backdrop-blur-sm">
                  <p className="text-xs text-blue-400 font-semibold uppercase tracking-wider mb-2">
                    Total Count
                  </p>
                  <p className="text-3xl font-bold bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">
                    {Object.values(stats[selectedColumn])
                      .reduce((a, b) => a + b, 0)
                      .toLocaleString()}
                  </p>
                </div>
                <div className="bg-gradient-to-br from-emerald-900/30 to-emerald-800/20 border border-emerald-700/30 rounded-xl p-4 backdrop-blur-sm">
                  <p className="text-xs text-emerald-400 font-semibold uppercase tracking-wider mb-2">
                    Unique Values
                  </p>
                  <p className="text-3xl font-bold bg-gradient-to-r from-emerald-400 to-green-400 bg-clip-text text-transparent">
                    {Object.keys(stats[selectedColumn]).length.toLocaleString()}
                  </p>
                </div>
                <div className="bg-gradient-to-br from-purple-900/30 to-purple-800/20 border border-purple-700/30 rounded-xl p-4 backdrop-blur-sm">
                  <p className="text-xs text-purple-400 font-semibold uppercase tracking-wider mb-2">
                    Most Common
                  </p>
                  <p
                    className="font-bold text-purple-300 text-lg truncate"
                    title={
                      Object.entries(stats[selectedColumn])[0]?.[0] || "N/A"
                    }
                  >
                    {Object.entries(stats[selectedColumn])[0]?.[0] || "N/A"}
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-16">
              <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-slate-800/50 border border-slate-700 mb-4">
                <BarChart3 className="w-8 h-8 text-slate-500" />
              </div>
              <p className="text-slate-400 font-medium">
                Select a column to view statistics
              </p>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
