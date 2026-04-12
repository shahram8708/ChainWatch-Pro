const ChainWatchAlerts = (() => {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
    let unreadPollingHandle = null;

    function getJSON(url, options = {}) {
        return fetch(url, {
            ...options,
            headers: {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
                ...(options.headers || {})
            }
        }).then(async (response) => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload?.message || `Request failed: ${response.status}`);
            }
            return payload;
        });
    }

    function getHTML(url) {
        return fetch(url, {
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                ...(csrfToken ? { "X-CSRFToken": csrfToken } : {})
            }
        }).then(async (response) => {
            const html = await response.text();
            if (!response.ok) {
                throw new Error(`Request failed: ${response.status}`);
            }
            return html;
        });
    }

    function updateGlobalAlertBadges(newCount) {
        const count = Math.max(Number(newCount || 0), 0);
        document.querySelectorAll(".js-alert-badge").forEach((badge) => {
            badge.textContent = String(count);
            badge.classList.toggle("d-none", count <= 0);
        });
    }

    function updateStatsDelta({ acknowledgedIncrement = 0, activeDecrement = 0 } = {}) {
        const acknowledged = document.querySelector("[data-stat='acknowledged_today']");
        const active = document.querySelector("[data-stat='total_active']");

        if (acknowledged) {
            const value = Number(acknowledged.textContent || 0) + acknowledgedIncrement;
            acknowledged.textContent = String(Math.max(value, 0));
        }

        if (active) {
            const value = Number(active.textContent || 0) - activeDecrement;
            active.textContent = String(Math.max(value, 0));
        }
    }

    function selectedFeedItems() {
        return Array.from(document.querySelectorAll(".alert-item"));
    }

    function setActiveAlertItem(alertId) {
        selectedFeedItems().forEach((item) => {
            item.classList.toggle("active", item.dataset.alertId === alertId);
        });
    }

    function currentParams() {
        return new URLSearchParams(window.location.search);
    }

    function updateUrl(params, replace = false) {
        const query = params.toString();
        const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}`;
        if (replace) {
            history.replaceState({}, "", nextUrl);
        } else {
            history.pushState({}, "", nextUrl);
        }
    }

    async function loadAlertDetail(alertId, pushState = true) {
        if (!alertId) {
            return;
        }

        const detailContainer = document.getElementById("alert-detail-container");
        if (!detailContainer) {
            return;
        }

        try {
            const html = await getHTML(`/alerts/${alertId}/detail`);
            detailContainer.innerHTML = html;
            setActiveAlertItem(alertId);

            const params = currentParams();
            params.set("selected_alert_id", alertId);
            if (pushState) {
                updateUrl(params, false);
            } else {
                updateUrl(params, true);
            }

            if (window.innerWidth < 1200) {
                document.body.classList.add("alert-mobile-detail-open");
            }

            bindAcknowledgeButton();
            bindRegenerateDescriptionButton();
            bindMobileBackButton();
        } catch (error) {
            console.error("Failed loading alert detail", error);
        }
    }

    function bindFeedSelection() {
        selectedFeedItems().forEach((item) => {
            item.addEventListener("click", (event) => {
                if (event.target.closest("a[data-alert-link='shipment']")) {
                    return;
                }
                const alertId = item.dataset.alertId;
                loadAlertDetail(alertId, true);
            });

            item.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    const alertId = item.dataset.alertId;
                    loadAlertDetail(alertId, true);
                }
            });
        });
    }

    async function loadFeed(params, pushState = true, autoSelectFirst = true) {
        const container = document.getElementById("alerts-feed-container");
        if (!container) {
            return;
        }

        params.set("partial", "feed");

        try {
            const html = await getHTML(`/alerts?${params.toString()}`);
            container.innerHTML = html;

            if (pushState) {
                params.delete("partial");
                updateUrl(params, false);
            }

            bindFeedSelection();
            bindPaginationButtons();
            bindTabFiltering();

            const totalCount = container.querySelector("#alert-feed-list")?.dataset.total;
            if (totalCount) {
                const totalElement = document.getElementById("alerts-total-count");
                if (totalElement) {
                    totalElement.textContent = totalCount;
                }
            }

            if (autoSelectFirst) {
                const firstItem = container.querySelector(".alert-item");
                const selectedId = currentParams().get("selected_alert_id");
                if (selectedId) {
                    const selectedItem = container.querySelector(`.alert-item[data-alert-id='${selectedId}']`);
                    if (selectedItem) {
                        setActiveAlertItem(selectedId);
                        await loadAlertDetail(selectedId, false);
                    } else if (firstItem) {
                        await loadAlertDetail(firstItem.dataset.alertId, true);
                    }
                } else if (firstItem) {
                    await loadAlertDetail(firstItem.dataset.alertId, true);
                }
            }

            setupAdaptiveUnreadPolling();
        } catch (error) {
            console.error("Failed loading alert feed", error);
        }
    }

    function bindPaginationButtons() {
        document.querySelectorAll(".js-alert-page").forEach((button) => {
            button.addEventListener("click", () => {
                const page = button.dataset.page;
                if (!page) {
                    return;
                }
                const params = currentParams();
                params.set("page", page);
                params.delete("selected_alert_id");
                loadFeed(params, true, true);
            });
        });
    }

    function bindTabFiltering() {
        document.querySelectorAll(".alert-severity-tab").forEach((tab) => {
            tab.addEventListener("click", () => {
                document.querySelectorAll(".alert-severity-tab").forEach((node) => {
                    node.classList.remove("active");
                });
                tab.classList.add("active");

                const severity = tab.dataset.severity || "all";
                const acknowledged = tab.dataset.acknowledged || "all";

                const params = currentParams();
                params.set("severity", severity);
                params.set("acknowledged", acknowledged);
                params.delete("selected_alert_id");
                params.set("page", "1");

                loadFeed(params, true, true);
            });
        });
    }

    function markFeedItemAcknowledged(alertId) {
        const item = document.querySelector(`.alert-item[data-alert-id='${alertId}']`);
        if (!item) {
            return;
        }

        item.dataset.acknowledged = "true";
        if (!item.querySelector(".bi-check-circle-fill")) {
            const metaBlock = item.querySelector(".text-end");
            if (metaBlock) {
                const icon = document.createElement("div");
                icon.className = "text-success";
                icon.innerHTML = '<i class="bi bi-check-circle-fill"></i>';
                metaBlock.appendChild(icon);
            }
        }
    }

    function bindAcknowledgeButton() {
        const button = document.getElementById("acknowledge-alert-btn");
        if (!button) {
            return;
        }

        button.addEventListener("click", async () => {
            const alertId = button.dataset.alertId;
            const ackUrl = button.dataset.ackUrl;
            if (!alertId || !ackUrl) {
                return;
            }

            button.disabled = true;
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Acknowledging';

            try {
                const payload = await getJSON(ackUrl, { method: "POST" });
                if (!payload.success) {
                    throw new Error(payload.message || "Could not acknowledge alert.");
                }

                const success = document.createElement("span");
                success.className = "text-success fw-semibold";
                success.textContent = `Acknowledged ✓ by ${payload.acknowledged_by} at ${payload.acknowledged_at}`;
                button.replaceWith(success);

                markFeedItemAcknowledged(alertId);
                updateStatsDelta({ acknowledgedIncrement: 1, activeDecrement: 1 });

                const currentBadge = document.querySelector(".js-alert-badge");
                const currentCount = Number(currentBadge?.textContent || 0);
                updateGlobalAlertBadges(Math.max(currentCount - 1, 0));
            } catch (error) {
                button.disabled = false;
                button.textContent = "Acknowledge";
                console.error("Failed acknowledging alert", error);
            }
        });
    }

    function bindRegenerateDescriptionButton() {
        const button = document.getElementById("regenerate-alert-description-btn");
        if (!button) {
            return;
        }

        button.addEventListener("click", async () => {
            const endpoint = button.dataset.regenerateUrl;
            if (!endpoint) {
                return;
            }

            const originalText = button.innerHTML;
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Regenerating...';
            button.disabled = true;

            try {
                const data = await getJSON(endpoint, { method: "POST" });
                if (!data.success) {
                    throw new Error(data.message || "Failed to regenerate alert description.");
                }

                const contentHost = document.getElementById("alert-description-content");
                if (contentHost) {
                    contentHost.innerHTML = data.content_html || `<p class=\"mb-0\">${data.description || ""}</p>`;
                }

                const timestampEl = document.getElementById("alert-ai-generated-at");
                if (timestampEl) {
                    timestampEl.textContent = "Just regenerated";
                }

                const regenCountEl = document.getElementById("alert-ai-regeneration-count");
                if (regenCountEl) {
                    regenCountEl.textContent = `Regenerated ${data.regeneration_count || 0} times`;
                }

                const staleBadge = document.getElementById("alert-ai-stale-warning");
                if (staleBadge) {
                    if (data.served_stale && data.stale_warning) {
                        staleBadge.textContent = data.stale_warning;
                        staleBadge.classList.remove("d-none");
                    } else {
                        staleBadge.classList.add("d-none");
                    }
                }

                const titleEl = document.querySelector(".alert-detail-content h3.h4");
                if (titleEl && data.title) {
                    titleEl.textContent = data.title;
                }

                button.innerHTML = "✓ Updated";
                button.classList.add("btn-success");
                setTimeout(() => {
                    button.innerHTML = originalText;
                    button.disabled = false;
                    button.classList.remove("btn-success");
                }, 2000);
            } catch (error) {
                console.error("Alert description regenerate failed", error);
                button.innerHTML = "✗ Failed - Try again";
                button.disabled = false;
                setTimeout(() => {
                    button.innerHTML = originalText;
                }, 3000);
            }
        });
    }

    function bindMarkAllRead() {
        const button = document.getElementById("mark-all-read-btn");
        if (!button) {
            return;
        }

        button.addEventListener("click", async () => {
            const unacknowledged = selectedFeedItems().filter((item) => item.dataset.acknowledged === "false");
            const alertIds = unacknowledged.map((item) => item.dataset.alertId).filter(Boolean);
            if (!alertIds.length) {
                return;
            }

            button.disabled = true;
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Updating';

            try {
                const payload = await getJSON("/alerts/acknowledge-bulk", {
                    method: "POST",
                    body: JSON.stringify({ alert_ids: alertIds })
                });

                if (!payload.success) {
                    throw new Error("Could not bulk acknowledge alerts.");
                }

                alertIds.forEach((id) => {
                    markFeedItemAcknowledged(id);
                });

                updateStatsDelta({ acknowledgedIncrement: payload.count || alertIds.length, activeDecrement: payload.count || alertIds.length });

                const currentBadge = document.querySelector(".js-alert-badge");
                const currentCount = Number(currentBadge?.textContent || 0);
                updateGlobalAlertBadges(Math.max(currentCount - (payload.count || alertIds.length), 0));

                const selectedId = currentParams().get("selected_alert_id");
                if (selectedId && alertIds.includes(selectedId)) {
                    loadAlertDetail(selectedId, false);
                }
            } catch (error) {
                console.error("Bulk acknowledge failed", error);
            } finally {
                button.disabled = false;
                button.textContent = "Mark All Read";
            }
        });
    }

    function hasUnacknowledgedCriticalAlerts() {
        return Boolean(document.querySelector(".alert-item.alert-severity-critical[data-acknowledged='false']"));
    }

    function bindRefreshBanner() {
        const refreshButton = document.getElementById("refresh-alert-feed-btn");
        if (!refreshButton) {
            return;
        }

        refreshButton.addEventListener("click", () => {
            const banner = document.getElementById("new-alert-banner");
            banner?.classList.add("d-none");
            const params = currentParams();
            loadFeed(params, false, false);
        });
    }

    function setupAdaptiveUnreadPolling() {
        if (unreadPollingHandle) {
            clearInterval(unreadPollingHandle);
        }

        const intervalMs = hasUnacknowledgedCriticalAlerts() ? 30000 : 60000;

        unreadPollingHandle = setInterval(async () => {
            try {
                const payload = await getJSON("/api/v1/alerts/unread-count", { method: "GET" });
                const unreadCount = Number(payload.count || 0);
                const currentRenderedCount = Number(document.getElementById("alert-feed-list")?.dataset.renderedCount || 0);

                if (unreadCount > currentRenderedCount) {
                    document.getElementById("new-alert-banner")?.classList.remove("d-none");
                }

                updateGlobalAlertBadges(unreadCount);
            } catch (error) {
                console.debug("Unread count poll failed", error);
            }
        }, intervalMs);
    }

    function bindMobileBackButton() {
        const backButton = document.getElementById("mobile-alert-back-btn");
        if (!backButton) {
            return;
        }

        backButton.addEventListener("click", () => {
            document.body.classList.remove("alert-mobile-detail-open");
        });
    }

    function bindPopState() {
        window.addEventListener("popstate", () => {
            const params = currentParams();
            const selectedAlertId = params.get("selected_alert_id");
            loadFeed(params, false, false).then(() => {
                if (selectedAlertId) {
                    loadAlertDetail(selectedAlertId, false);
                }
            });
        });
    }

    function init() {
        const layout = document.getElementById("alert-center-layout");
        if (!layout) {
            return;
        }

        bindFeedSelection();
        bindPaginationButtons();
        bindTabFiltering();
        bindAcknowledgeButton();
        bindRegenerateDescriptionButton();
        bindMarkAllRead();
        bindRefreshBanner();
        bindMobileBackButton();
        bindPopState();
        setupAdaptiveUnreadPolling();

        window.addEventListener("resize", () => {
            if (window.innerWidth >= 1200) {
                document.body.classList.remove("alert-mobile-detail-open");
            }
        });
    }

    return {
        init
    };
})();

document.addEventListener("DOMContentLoaded", () => {
    ChainWatchAlerts.init();
});

export default ChainWatchAlerts;
