import { request } from "../request";

export interface LocalExpertInput {
  name: string;
  description: string;
  system_prompt: string;
  default_model: string;
  actions: string[];
  skill_package_ids: string[];
  knowledge_base_ids: string[];
}
export interface LocalExpert extends LocalExpertInput {
  agent_id: string;
  published_expert_id: string | null;
}
export interface LocalExpertResources {
  models: { id: string; name: string }[];
  actions: { id: string; name: string }[];
  skills: { id: string; name: string }[];
  knowledge_bases: { id: string; name: string }[];
}
export interface OfflineExpertPackage {
  name: string;
  description: string;
  version: string;
  source: string;
  license: string;
  system_prompt: string;
  actions: string[];
  skills: { name: string; content: string; source: string; license: string }[];
}
export interface LocalExpertRelease extends OfflineExpertPackage {
  id: string;
}
const base = "/experts/local";
const json = (method: string, body: unknown) => ({
  method,
  body: JSON.stringify(body),
});
export const localExpertsApi = {
  list: () => request<LocalExpert[]>(base),
  resources: () => request<LocalExpertResources>(`${base}/resources`),
  create: (body: LocalExpertInput) =>
    request<LocalExpert>(base, json("POST", body)),
  update: (id: string, body: LocalExpertInput) =>
    request<LocalExpert>(
      `${base}/${encodeURIComponent(id)}`,
      json("PUT", body),
    ),
  releases: () => request<LocalExpertRelease[]>(`${base}/releases`),
  install: (id: string, body: LocalExpertInput) =>
    request<LocalExpert>(
      `${base}/releases/${encodeURIComponent(id)}/install`,
      json("POST", body),
    ),
  export: (
    id: string,
    body: { version: string; source: string; license: string },
  ) =>
    request<OfflineExpertPackage>(
      `${base}/${encodeURIComponent(id)}/export`,
      json("POST", body),
    ),
  preview: (body: unknown) =>
    request<OfflineExpertPackage>(
      `${base}/releases/preview`,
      json("POST", body),
    ),
  publish: (pack: OfflineExpertPackage) =>
    request<LocalExpertRelease>(
      `${base}/releases`,
      json("POST", { package: pack, reviewed: true }),
    ),
};
