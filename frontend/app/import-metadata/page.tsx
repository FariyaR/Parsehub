"use client";
import apiClient from "@/lib/apiClient";
import { getApiHeaders } from "@/lib/apiBase";
import { getResponseMessage, readResponseData } from "@/lib/response";

import { useEffect, useState, useRef } from "react";
import {
  Upload,
  Download,
  CheckCircle,
  AlertCircle,
  Clock,
  Trash2,
  Eye,
  EyeOff,
} from "lucide-react";
import Header from "@/components/Header";

interface PreviewRow {
  name: string;
  value: string;
  status: "valid" | "error";
  error?: string;
}

interface ImportResult {
  batch_id?: number;
  file_name?: string;
  stats?: {
    total_records: number;
    imported: number;
    skipped: number;
    errors: Array<{
      row: number;
      error: string;
      personal_id: string;
    }>;
  };
  success: boolean;
  error?: string;
}

interface ImportHistory {
  id: number;
  file_name: string;
  record_count: number;
  status: string;
  upload_date: string;
}

const TEMPLATE_HEADERS = [
  "Personal Project ID",
  "Project ID (ParseHub)",
  "Project_name",
  "Last_run_data",
  "Create_date",
  "update_date",
  "Region",
  "Country",
  "Brand",
  "Website_url",
  "Total_pages",
  "Total_products",
  "Current_page_scraped",
  "current_product_scraped",
];

export default function ImportMetadataPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(true);
  const [importHistory, setImportHistory] = useState<ImportHistory[]>([]);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    fetchImportHistory();
  }, []);

  const fetchImportHistory = async () => {
    try {
      console.log("[Import] Fetching import history...");
      
      const response = await fetch("/api/import-history", {
        headers: getApiHeaders(),
      });

      console.log("[Import] Import history response status:", response.status);
      const data = await readResponseData<{
        count?: number;
        batches?: ImportHistory[];
        error?: string;
        message?: string;
      }>(response);

      if (response.ok) {
        console.log("[Import] Successfully fetched import history -", data.count || 0, "batches");
        setImportHistory(data.batches || []);
      } else {
        console.error(
          "[Import] Failed to fetch history:",
          response.status,
          getResponseMessage(data, response.statusText),
        );
      }
    } catch (err) {
      console.error("[Import] Error fetching history:", err);
    }
  };

  const handleDrag = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const droppedFile = e.dataTransfer.files[0];
      if (droppedFile.name.endsWith(".xlsx") || droppedFile.name.endsWith(".xls")) {
        setFile(droppedFile);
        setError(null);
      } else {
        setError("Please upload an Excel file (.xlsx or .xls)");
      }
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const selectedFile = e.target.files[0];
      if (selectedFile.name.endsWith(".xlsx") || selectedFile.name.endsWith(".xls")) {
        setFile(selectedFile);
        setError(null);
      } else {
        setError("Please upload an Excel file (.xlsx or .xls)");
      }
    }
  };

  const downloadTemplate = () => {
    const csv = TEMPLATE_HEADERS.join(",") + "\n";
    const element = document.createElement("a");
    element.setAttribute(
      "href",
      "data:text/csv;charset=utf-8," + encodeURIComponent(csv)
    );
    element.setAttribute("download", "metadata_template.csv");
    element.style.display = "none";
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  const handleImport = async () => {
    if (!file) {
      setError("Please select a file first");
      console.warn("[Import] No file selected");
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      console.log("[Import] Starting import for file:", file.name, "Size:", file.size, "bytes");
      
      const formData = new FormData();
      formData.append("file", file);
      formData.append("uploaded_by", "web_ui");

      const response = await fetch("/api/metadata/import", {
        method: "POST",
        headers: getApiHeaders(),
        body: formData,
      });

      console.log("[Import] Response status:", response.status, response.statusText);

      const data = await readResponseData<ImportResult>(response);
      console.log("[Import] Response data:", data);

      if (response.ok && data.success) {
        console.log("[Import] Successfully imported", data.stats?.imported, "records");
        setResult(data);
        setFile(null);
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
        await fetchImportHistory();
      } else {
        const errorMsg = getResponseMessage(data, "Import failed");
        console.error("[Import] Import failed:", errorMsg, data);
        setError(errorMsg);
        setResult(data);
      }
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      console.error("[Import] Error:", errorMsg, err);
      setError(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  const resetForm = () => {
    setFile(null);
    setResult(null);
    setError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950">
      <Header />

      <section className="container mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-slate-100 mb-2">Import Metadata</h1>
          <p className="text-slate-400">Upload project metadata from Excel files</p>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="mb-6 bg-red-900/30 backdrop-blur-sm border border-red-700/50 rounded-xl p-4 flex items-start gap-3 shadow-lg shadow-red-900/20">
            <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-red-300">Error</p>
              <p className="text-red-400 text-sm mt-0.5">{error}</p>
            </div>
          </div>
        )}

        {/* Success Alert */}
        {result?.success && (
          <div className="mb-6 bg-green-900/30 backdrop-blur-sm border border-green-700/50 rounded-xl p-4 flex items-start gap-3 shadow-lg shadow-green-900/20">
            <CheckCircle className="w-5 h-5 text-green-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="font-semibold text-green-300">Import Successful</p>
              <p className="text-green-400 text-sm mt-0.5">
                {result.stats?.imported} records imported, {result.stats?.skipped} skipped
              </p>
              {result.stats?.errors && result.stats.errors.length > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-green-300 text-sm">
                    View errors ({result.stats.errors.length})
                  </summary>
                  <div className="mt-2 space-y-1">
                    {result.stats.errors.map((err, idx) => (
                      <p key={idx} className="text-xs text-green-400">
                        Row {err.row}: {err.error}
                      </p>
                    ))}
                  </div>
                </details>
              )}
            </div>
            <button
              onClick={resetForm}
              className="px-3 py-1 bg-green-700 hover:bg-green-600 rounded text-sm text-white"
            >
              Import Another
            </button>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Upload Section */}
          <div className="lg:col-span-2">
            <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-xl p-8">
              <h2 className="text-xl font-semibold text-slate-100 mb-6">Upload File</h2>

              {/* Template Download */}
              <div className="mb-6 p-4 bg-blue-900/20 border border-blue-700/50 rounded-lg flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-blue-300">Need help?</p>
                  <p className="text-xs text-blue-400 mt-1">
                    Download the template to see the expected format
                  </p>
                </div>
                <button
                  onClick={downloadTemplate}
                  className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-white text-sm font-medium transition-colors"
                >
                  <Download className="w-4 h-4" />
                  Template
                </button>
              </div>

              {/* File Upload Area */}
              <div
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
                className={`relative border-2 border-dashed rounded-xl p-12 text-center transition-all duration-200 ${
                  dragActive
                    ? "border-blue-500 bg-blue-500/10"
                    : "border-slate-600 hover:border-slate-500 bg-slate-800/50"
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  onChange={handleFileChange}
                  accept=".xlsx,.xls"
                  className="hidden"
                  disabled={loading}
                />

                <div className="flex flex-col items-center gap-4">
                  <div className="p-3 bg-slate-700/50 rounded-lg">
                    <Upload className="w-8 h-8 text-slate-400" />
                  </div>
                  <div>
                    <p className="text-slate-100 font-semibold">
                      {file ? file.name : "Drag and drop your file here"}
                    </p>
                    <p className="text-slate-400 text-sm mt-1">
                      or{" "}
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        disabled={loading}
                        className="text-blue-400 hover:text-blue-300 font-medium"
                      >
                        browse
                      </button>
                    </p>
                    <p className="text-slate-500 text-xs mt-2">
                      Supports .xlsx and .xls files
                    </p>
                  </div>
                </div>
              </div>

              {/* File Selected Info */}
              {file && !result && (
                <div className="mt-6 p-4 bg-slate-700/50 border border-slate-600/50 rounded-lg">
                  <p className="text-sm text-slate-200 font-medium">Selected File</p>
                  <p className="text-slate-400 text-sm mt-1">{file.name}</p>
                  <p className="text-slate-500 text-xs mt-1">
                    {(file.size / 1024).toFixed(2)} KB
                  </p>
                </div>
              )}

              {/* Import Button */}
              {!result && (
                <div className="mt-6 flex gap-3">
                  <button
                    onClick={handleImport}
                    disabled={!file || loading}
                    className="flex-1 inline-flex items-center justify-center gap-2 px-6 py-3 bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-700 hover:to-blue-800 disabled:from-slate-700 disabled:to-slate-700 rounded-lg font-semibold transition-all duration-200 disabled:cursor-not-allowed text-white"
                  >
                    {loading ? (
                      <>
                        <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        Importing...
                      </>
                    ) : (
                      <>
                        <Upload className="w-5 h-5" />
                        Import File
                      </>
                    )}
                  </button>
                  {file && (
                    <button
                      onClick={resetForm}
                      disabled={loading}
                      className="px-4 py-3 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-700/50 rounded-lg text-slate-300 font-medium transition-colors disabled:cursor-not-allowed"
                    >
                      Clear
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Info Panel */}
          <div className="space-y-6">
            {/* Expected Format */}
            <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
              <h3 className="text-lg font-semibold text-slate-100 mb-4">Expected Format</h3>
              <div className="space-y-2 text-sm">
                <p className="text-slate-400">Required columns:</p>
                <ul className="space-y-1 text-slate-400 ml-4">
                  <li>• Personal Project ID</li>
                  <li>• Project_name</li>
                  <li>• Total_pages</li>
                </ul>
                <p className="text-slate-400 mt-4 pt-4 border-t border-slate-700">
                  Optional columns:
                </p>
                <ul className="space-y-1 text-slate-400 ml-4 text-xs">
                  <li>• Region, Country, Brand</li>
                  <li>• Website_url</li>
                  <li>• Total_products</li>
                  <li>• Current_page_scraped</li>
                </ul>
              </div>
            </div>

            {/* Stats */}
            {result?.stats && (
              <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700/50 rounded-xl p-6">
                <h3 className="text-lg font-semibold text-slate-100 mb-4">Import Results</h3>
                <div className="space-y-3">
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400">Total Records:</span>
                    <span className="text-slate-100 font-semibold">
                      {result.stats.total_records}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400">Imported:</span>
                    <span className="text-green-400 font-semibold">
                      {result.stats.imported}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400">Skipped:</span>
                    <span className="text-yellow-400 font-semibold">
                      {result.stats.skipped}
                    </span>
                  </div>
                  {result.stats.errors.length > 0 && (
                    <div className="flex justify-between items-center">
                      <span className="text-slate-400">Errors:</span>
                      <span className="text-red-400 font-semibold">
                        {result.stats.errors.length}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Import History */}
        <div className="mt-12">
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="flex items-center gap-2 px-6 py-3 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg font-medium text-slate-300 transition-colors"
          >
            {showHistory ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
            {showHistory ? "Hide" : "Show"} Import History
          </button>

          {showHistory && (
            <div className="mt-6 overflow-x-auto rounded-xl border border-slate-700/50 bg-slate-800/30 backdrop-blur-sm">
              {importHistory.length === 0 ? (
                <div className="p-8 text-center text-slate-400">
                  <Clock className="w-12 h-12 mx-auto mb-3 opacity-50" />
                  <p>No import history yet</p>
                </div>
              ) : (
                <table className="w-full">
                  <thead className="bg-slate-800/60 border-b border-slate-700/50">
                    <tr>
                      <th className="px-6 py-3 text-left text-sm font-semibold text-slate-300">
                        File Name
                      </th>
                      <th className="px-6 py-3 text-center text-sm font-semibold text-slate-300">
                        Records
                      </th>
                      <th className="px-6 py-3 text-left text-sm font-semibold text-slate-300">
                        Status
                      </th>
                      <th className="px-6 py-3 text-left text-sm font-semibold text-slate-300">
                        Uploaded
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/50">
                    {importHistory.map((batch) => (
                      <tr key={batch.id} className="hover:bg-slate-700/30">
                        <td className="px-6 py-4 text-sm text-slate-200">{batch.file_name}</td>
                        <td className="px-6 py-4 text-sm text-center text-slate-400">
                          {batch.record_count}
                        </td>
                        <td className="px-6 py-4 text-sm">
                          <span
                            className={`px-3 py-1 rounded-full text-xs font-medium ${
                              batch.status === "success"
                                ? "bg-green-900/30 text-green-300"
                                : "bg-red-900/30 text-red-300"
                            }`}
                          >
                            {batch.status}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm text-slate-400">
                          {new Date(batch.upload_date).toLocaleDateString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
