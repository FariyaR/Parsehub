"use client";
import apiClient from "@/lib/apiClient";
import { getApiHeaders } from "@/lib/apiBase";
import { getResponseMessage, readResponseData } from "@/lib/response";
import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  ChevronRight,
  Play,
  Clock,
  BarChart3,
  FileJson,
  PlayCircle,
  Layers,
  CheckCircle2,
  Loader2,
  AlertCircle,
  X,
  ExternalLink,
} from "lucide-react";
import SchedulerModal from "./SchedulerModal";
import ColumnStatisticsModal from "./ColumnStatisticsModal";
import CSVDataModal from "./CSVDataModal";

interface Project {
  token: string;
  name?: string;
  title?: string;
  owner_email?: string;
  projecturl?: string;
  main_site?: string;
  last_run?: {
    status: string;
    pages: number;
    start_time: string;
    run_token: string;
  } | null;
}

interface ProjectsListProps {
  projects: Project[];
  onRunProject: (token: string) => Promise<void>;
}

export default function ProjectsList({
  projects,
  onRunProject,
}: ProjectsListProps) {
  const router = useRouter();
  const [groupedByBrand, setGroupedByBrand] = useState<Map<string, Project[]>>(
    new Map(),
  );
  const [expandedBrands, setExpandedBrands] = useState<Set<string>>(new Set());
  const [pageInputs, setPageInputs] = useState<{ [key: string]: string }>({});
  const [loading, setLoading] = useState<string | null>(null);
  const [showScheduler, setShowScheduler] = useState(false);
  const [selectedProjectForSchedule, setSelectedProjectForSchedule] = useState<
    string | null
  >(null);
  const [showStatsModal, setShowStatsModal] = useState(false);
  const [showCSVModal, setShowCSVModal] = useState(false);
  const [selectedProjectToken, setSelectedProjectToken] = useState<
    string | null
  >(null);
  const [selectedProjectName, setSelectedProjectName] = useState<string | null>(
    null,
  );

  useEffect(() => {
    // Helper function to extract website domain from project name
    // Pattern: "(Brand Name) ... website_domain_productname"
    // Examples:
    // "(MSA Pricing) Filter-technik.de_Kraftstoffvorfilter" -> "Filter-technik.de"
    // "(Brand) example.com_product" -> "example.com"
    const extractWebsite = (projectName: string): string => {
      // Match pattern: ) followed by domain (with dots), followed by _
      const match = projectName.match(/\)\s*([^_\s]+(?:\.[^_\s]+)*?)_/);
      if (match && match[1]) {
        return match[1];
      }
      // Fallback: use first 30 chars or project name
      return projectName.substring(0, 30) || "Other";
    };

    // Group projects by website domain
    const groups = new Map<string, Project[]>();

    projects.forEach((project) => {
      const projectName = project.name || project.title || "Unknown";
      // Extract website domain from project name
      const website = extractWebsite(projectName);

      if (!groups.has(website)) {
        groups.set(website, []);
      }
      // Preserve all project data including last_run
      groups.get(website)!.push({
        ...project,
        name: projectName,
      });
    });

    setGroupedByBrand(groups);
  }, [projects]);

  const toggleBrand = (brand: string) => {
    setExpandedBrands((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(brand)) {
        newSet.delete(brand);
      } else {
        newSet.add(brand);
      }
      return newSet;
    });
  };

  const handleRunProject = async (token: string) => {
    setLoading(token);
    try {
      await onRunProject(token);
    } finally {
      setLoading(null);
    }
  };

  const handleRunAll = async (brand: string) => {
    const brandProjects = groupedByBrand.get(brand);
    if (!brandProjects) return;

    for (const project of brandProjects) {
      try {
        await handleRunProject(project.token);
        await new Promise((resolve) => setTimeout(resolve, 1000));
      } catch (error) {
        console.error(`Failed to run ${project.name}:`, error);
      }
    }
  };

  const handleScheduleClick = (token: string) => {
    setSelectedProjectForSchedule(token);
    setShowScheduler(true);
  };

  const handleViewStats = (token: string, name: string) => {
    setSelectedProjectToken(token);
    setSelectedProjectName(name);
    setShowStatsModal(true);
  };

  const handleViewCSV = (token: string, name: string) => {
    setSelectedProjectToken(token);
    setSelectedProjectName(name);
    setShowCSVModal(true);
  };

  const handleCancelRun = async (token: string) => {
    setLoading(token);
    try {
      const response = await fetch(`/api/runs/${token}/cancel`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getApiHeaders(),
        },
      });
      const data = await readResponseData<Record<string, unknown>>(response);

      if (!response.ok) {
        throw new Error(
          getResponseMessage(data, `Failed to cancel run: ${response.statusText}`),
        );
      }

      console.log("Run cancelled successfully:", data);

      // Trigger a refresh of projects to update status
      if (onRunProject) {
        // Force a refresh by calling the parent's refresh mechanism
        window.dispatchEvent(new CustomEvent("projectStatusUpdated"));
      }
    } catch (error) {
      console.error("Error cancelling run:", error);
      alert(
        `Failed to cancel run: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    } finally {
      setLoading(null);
    }
  };

  const handlePageChange = (token: string, value: string) => {
    setPageInputs((prev) => ({
      ...prev,
      [token]: value,
    }));
  };

  const getStatusBadge = (status?: string) => {
    if (!status) return null;

    const statusConfig = {
      complete: {
        icon: CheckCircle2,
        label: "Completed",
        className: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
      },
      running: {
        icon: Loader2,
        label: "Running",
        className: "bg-blue-500/20 text-blue-400 border-blue-500/30",
      },
      queued: {
        icon: Clock,
        label: "Queued",
        className: "bg-amber-500/20 text-amber-400 border-amber-500/30",
      },
      error: {
        icon: AlertCircle,
        label: "Error",
        className: "bg-red-500/20 text-red-400 border-red-500/30",
      },
    };

    const config =
      statusConfig[status as keyof typeof statusConfig] || statusConfig.queued;
    const Icon = config.icon;

    return (
      <span
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${config.className}`}
      >
        <Icon
          size={12}
          className={status === "running" ? "animate-spin" : ""}
        />
        {config.label}
      </span>
    );
  };

  if (groupedByBrand.size === 0) {
    return (
      <div className="text-center py-16">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-slate-800/50 border border-slate-700 mb-4">
          <Layers className="w-8 h-8 text-slate-500" />
        </div>
        <p className="text-slate-400 text-lg font-medium">No projects found</p>
        <p className="text-slate-500 text-sm mt-2">
          Start by creating a new project
        </p>
      </div>
    );
  }

  return (
    <div className="bg-slate-800/30 backdrop-blur-sm rounded-xl border border-slate-700/50 overflow-hidden shadow-2xl">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-gradient-to-r from-slate-800 to-slate-800/80 border-b border-slate-700/50">
              <th className="px-4 py-4 text-left w-10"></th>
              <th className="px-6 py-4 text-left text-xs font-semibold text-slate-300 uppercase tracking-wider">
                Project Name
              </th>
              <th className="px-6 py-4 text-left text-xs font-semibold text-slate-300 uppercase tracking-wider w-32">
                Status
              </th>
              <th className="px-6 py-4 text-center text-xs font-semibold text-slate-300 uppercase tracking-wider w-28">
                Total Pages
              </th>
              <th className="px-4 py-4 text-center text-xs font-semibold text-slate-300 uppercase tracking-wider w-32">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/30">
            {Array.from(groupedByBrand.entries())
              .sort(([brandA], [brandB]) => brandA.localeCompare(brandB))
              .map(([brand, brandProjects]) => (
                <React.Fragment key={brand}>
                  {/* Brand Row */}
                  <tr
                    className="bg-slate-800/60 hover:bg-slate-700/40 transition-all duration-200 cursor-pointer group"
                    onClick={() => toggleBrand(brand)}
                  >
                    <td className="px-4 py-3.5">
                      <div className="flex items-center justify-center">
                        {expandedBrands.has(brand) ? (
                          <ChevronDown className="w-4 h-4 text-blue-400 transition-transform duration-200" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-slate-400 group-hover:text-slate-300 transition-colors" />
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 text-white font-bold text-sm shadow-lg">
                          {brand.charAt(0).toUpperCase()}
                        </div>
                        <div>
                          <div className="font-semibold text-slate-100 text-base">
                            {brand}
                          </div>
                          <div className="text-xs text-slate-400 mt-0.5">
                            {brandProjects.length} project
                            {brandProjects.length !== 1 ? "s" : ""}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-3.5">
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-slate-700/50 text-slate-300 border border-slate-600/50">
                        <Layers size={12} />
                        Group
                      </span>
                    </td>
                    <td className="px-6 py-3.5 text-center">
                      {(() => {
                        const totalPages = brandProjects.reduce(
                          (sum, p) => sum + (p.last_run?.pages || 0),
                          0,
                        );
                        return totalPages > 0 ? (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-700/50 text-slate-200 font-semibold text-sm border border-slate-600/50">
                            {totalPages.toLocaleString()}
                          </span>
                        ) : (
                          <span className="text-slate-500 text-sm">—</span>
                        );
                      })()}
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex justify-center">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleRunAll(brand);
                          }}
                          disabled={loading !== null}
                          className="inline-flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-emerald-600 to-emerald-700 hover:from-emerald-700 hover:to-emerald-800 disabled:from-slate-700 disabled:to-slate-700 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-lg hover:shadow-emerald-500/25 disabled:shadow-none"
                        >
                          <PlayCircle size={16} />
                          Run All
                        </button>
                      </div>
                    </td>
                  </tr>

                  {/* Sub-Projects Rows */}
                  {expandedBrands.has(brand) &&
                    brandProjects.map((project, index) => (
                      <tr
                        key={project.token}
                        className="bg-slate-900/30 hover:bg-slate-800/40 transition-all duration-150 border-l-2 border-transparent hover:border-blue-500/50 cursor-pointer"
                        onClick={() =>
                          router.push(`/projects/${project.token}`)
                        }
                      >
                        <td className="px-4 py-4"></td>
                        <td className="px-6 py-4">
                          <div className="flex items-start gap-3">
                            <div className="flex items-center justify-center w-6 h-6 rounded bg-slate-700/50 text-slate-400 text-xs font-medium mt-0.5">
                              {index + 1}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div
                                className="font-medium text-blue-400 hover:text-blue-300 text-sm mb-1.5 truncate cursor-pointer"
                                title={project.name}
                              >
                                {project.name}
                              </div>
                              <div className="flex items-center gap-2 flex-wrap">
                                <code className="inline-flex items-center gap-1 bg-slate-800/80 px-2 py-0.5 rounded text-xs text-slate-400 font-mono border border-slate-700/50">
                                  <span className="text-slate-500">•</span>{" "}
                                  {project.token}
                                </code>
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-4">
                          {getStatusBadge(project.last_run?.status)}
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex items-center justify-center gap-2">
                            {project.last_run && project.last_run.pages > 0 ? (
                              <>
                                <span className="inline-flex items-center px-3 py-1.5 rounded-lg bg-blue-500/10 text-blue-400 font-semibold text-sm border border-blue-500/20">
                                  {project.last_run.pages}
                                </span>
                                <input
                                  type="number"
                                  min="1"
                                  placeholder="New"
                                  value={pageInputs[project.token] || ""}
                                  onChange={(e) =>
                                    handlePageChange(
                                      project.token,
                                      e.target.value,
                                    )
                                  }
                                  className="w-16 px-2 py-1.5 bg-slate-800/50 border border-slate-600/50 rounded-lg text-slate-300 text-xs font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-all placeholder:text-slate-500"
                                  onClick={(e) => e.stopPropagation()}
                                />
                              </>
                            ) : (
                              <input
                                type="number"
                                min="1"
                                placeholder="Enter pages"
                                value={pageInputs[project.token] || ""}
                                onChange={(e) =>
                                  handlePageChange(
                                    project.token,
                                    e.target.value,
                                  )
                                }
                                className="w-28 px-3 py-2 bg-slate-800/50 border border-slate-600/50 rounded-lg text-slate-200 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-all placeholder:text-slate-500"
                                onClick={(e) => e.stopPropagation()}
                              />
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-4">
                          <div className="flex items-center justify-center gap-2">
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                router.push(`/projects/${project.token}`);
                              }}
                              className="inline-flex items-center gap-1.5 px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-indigo-500/25"
                              title="View Details"
                            >
                              <ExternalLink size={14} />
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleRunProject(project.token);
                              }}
                              disabled={loading === project.token}
                              className="inline-flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-700 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-blue-500/25 disabled:shadow-none"
                              title="Run Project"
                            >
                              {loading === project.token ? (
                                <Loader2 size={14} className="animate-spin" />
                              ) : (
                                <Play size={14} />
                              )}
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleScheduleClick(project.token);
                              }}
                              className="inline-flex items-center gap-1.5 px-3 py-2 bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-purple-500/25"
                              title="Schedule"
                            >
                              <Clock size={14} />
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleViewStats(
                                  project.token,
                                  project.name || "",
                                );
                              }}
                              className="inline-flex items-center gap-1.5 px-3 py-2 bg-orange-600 hover:bg-orange-700 text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-orange-500/25"
                              title="Statistics"
                            >
                              <BarChart3 size={14} />
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleViewCSV(
                                  project.token,
                                  project.name || "",
                                );
                              }}
                              className="inline-flex items-center gap-1.5 px-3 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-emerald-500/25"
                              title="View CSV"
                            >
                              <FileJson size={14} />
                            </button>
                            {project.last_run?.status === "running" && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleCancelRun(project.token);
                                }}
                                disabled={loading === project.token}
                                className="inline-flex items-center gap-1.5 px-3 py-2 bg-red-600 hover:bg-red-700 disabled:bg-slate-700 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-red-500/25 disabled:shadow-none"
                                title="Cancel Run"
                              >
                                {loading === project.token ? (
                                  <Loader2 size={14} className="animate-spin" />
                                ) : (
                                  <X size={14} />
                                )}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                </React.Fragment>
              ))}
          </tbody>
        </table>
      </div>

      {/* Scheduler Modal */}
      {showScheduler && selectedProjectForSchedule && (
        <SchedulerModal
          projectToken={selectedProjectForSchedule}
          onClose={() => {
            setShowScheduler(false);
            setSelectedProjectForSchedule(null);
          }}
          onSchedule={(time) => {
            console.log(`Scheduled for ${time}`);
            setShowScheduler(false);
          }}
        />
      )}

      {/* Statistics Modal */}
      {showStatsModal && selectedProjectToken && selectedProjectName && (
        <ColumnStatisticsModal
          token={selectedProjectToken}
          title={selectedProjectName}
          isOpen={showStatsModal}
          onClose={() => {
            setShowStatsModal(false);
            setSelectedProjectToken(null);
            setSelectedProjectName(null);
          }}
        />
      )}

      {/* CSV Data Modal */}
      {showCSVModal && selectedProjectToken && selectedProjectName && (
        <CSVDataModal
          token={selectedProjectToken}
          title={selectedProjectName}
          isOpen={showCSVModal}
          onClose={() => {
            setShowCSVModal(false);
            setSelectedProjectToken(null);
            setSelectedProjectName(null);
          }}
        />
      )}
    </div>
  );
}
