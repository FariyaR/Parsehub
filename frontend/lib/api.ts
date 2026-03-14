import { getFrontendApiUrl, getApiHeaders } from "./apiBase";
import { getResponseMessage, readResponseData } from "./response";
export const API_URL = getFrontendApiUrl();

export async function fetchProjects() {
  const response = await fetch(`${API_URL}/api/projects`, {
    headers: getApiHeaders(),
  });
  const data = await readResponseData(response);
  if (!response.ok) throw new Error(getResponseMessage(data, 'Failed to fetch projects'));
  return data;
}

export async function runProject(token: string) {
  const response = await fetch(`${API_URL}/api/projects/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
    body: JSON.stringify({ token }),
  });
  const data = await readResponseData(response);
  if (!response.ok) throw new Error(getResponseMessage(data, 'Failed to run project'));
  return data;
}

export async function runAllProjects() {
  const response = await fetch(`${API_URL}/api/projects/run-all`, {
    method: 'POST',
    headers: getApiHeaders(),
  });
  const data = await readResponseData(response);
  if (!response.ok) throw new Error(getResponseMessage(data, 'Failed to run all projects'));
  return data;
}

export async function getRunData(token: string, runToken: string) {
  const response = await fetch(
    `${API_URL}/api/projects/${token}/${runToken}`,
    { headers: getApiHeaders() }
  );
  const data = await readResponseData(response);
  if (!response.ok) throw new Error(getResponseMessage(data, 'Failed to fetch run data'));
  return data;
}
