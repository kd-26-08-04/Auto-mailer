const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

async function request(endpoint, options = {}) {
  const url = endpoint.startsWith("http") ? endpoint : `${API_BASE}${endpoint}`;
  
  const headers = options.headers || {};
  if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  headers["Accept"] = "application/json";

  const config = {
    ...options,
    headers,
    credentials: "include", // Send session cookies cross-origin
  };

  const response = await fetch(url, config);
  let data;
  try {
    data = await response.json();
  } catch (err) {
    if (!response.ok) {
      throw new Error(`Server returned HTTP ${response.status}`);
    }
    data = { success: true };
  }

  if (!response.ok && !data.success) {
    throw new Error(data.error || data.message || `Request failed with status ${response.status}`);
  }

  return data;
}

export const api = {
  // Auth — 401 on checkAuth is normal when user is not logged in
  checkAuth: () => request("/api/auth/me").catch(() => ({ authenticated: false })),
  login: (credentials) => request("/login", { method: "POST", body: JSON.stringify(credentials) }),
  register: (userData) => request("/register", { method: "POST", body: JSON.stringify(userData) }),
  logout: () => request("/logout", { method: "POST" }),
  
  // Settings & Credentials
  getSettings: () => request("/settings"),
  saveSettings: (formData) => request("/settings", { method: "POST", body: formData }),
  
  // Scan Inbox Replies
  checkReplies: (formData) => request("/check-replies", { method: "POST", body: formData }),
  
  // Classic Mailer Controls
  getStatus: () => request("/status"),
  getRecipients: (batchId) => request(`/recipients?batch_id=${encodeURIComponent(batchId)}`),
  previewOutreach: (formData) => request("/preview", { method: "POST", body: formData }),
  startOutreach: (formData) => request("/start", { method: "POST", body: formData }),
  stopOutreach: () => request("/stop", { method: "POST" }),

  // Sequences API
  getTemplates: () => request("/api/templates"),
  getTemplate: (id) => request(`/api/templates/${id}`),
  saveTemplate: (id, payload) => request(`/api/templates/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  resetTemplate: (id) => request(`/api/templates/${id}/reset`, { method: "POST" }),

  getSequences: () => request("/api/sequences"),
  getSequence: (id) => request(`/api/sequences/${id}`),
  createSequence: (payload) => request("/api/sequences", { method: "POST", body: JSON.stringify(payload) }),
  updateSequence: (id, payload) => request(`/api/sequences/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteSequence: (id) => request(`/api/sequences/${id}`, { method: "DELETE" }),

  parseLeads: (formData) => request("/api/parse-leads", { method: "POST", body: formData }),
  activateSequence: (id, payloadOrFormData) => {
    const isFD = payloadOrFormData instanceof FormData;
    return request(`/api/sequences/${id}/activate`, {
      method: "POST",
      body: isFD ? payloadOrFormData : JSON.stringify(payloadOrFormData),
    });
  },
  enrollSequence: (id, payloadOrFormData) => {
    const isFD = payloadOrFormData instanceof FormData;
    return request(`/api/sequences/${id}/enroll`, {
      method: "POST",
      body: isFD ? payloadOrFormData : JSON.stringify(payloadOrFormData),
    });
  },
  pauseSequence: (id) => request(`/api/sequences/${id}/pause`, { method: "POST" }),
  resumeSequence: (id) => request(`/api/sequences/${id}/resume`, { method: "POST" }),
  
  getSequenceDashboard: (id) => request(id ? `/api/sequences/${id}/dashboard` : "/api/sequences/dashboard"),
  getInbox: (id) => request(id ? `/api/sequences/${id}/inbox` : "/api/inbox"),
  getAnalytics: (id) => request(id ? `/api/sequences/${id}/analytics` : "/api/analytics"),

  previewSequence: (id, formData) => request(`/api/sequences/${id}/preview`, { method: "POST", body: formData }),

  uploadAttachment: (id, formData) =>
    request(`/api/sequences/${id}/attachments`, { method: "POST", body: formData }),
  listAttachments: (id) => request(`/api/sequences/${id}/attachments`),
  deleteAttachment: (id, attachmentId) =>
    request(`/api/sequences/${id}/attachments/${attachmentId}`, { method: "DELETE" }),
};
