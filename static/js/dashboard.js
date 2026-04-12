const ChainWatchDashboard = (() => {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
    let filterDebounceHandle = null;
    let globalSearchDebounceHandle = null;
    let globalSearchAbortController = null;

    function getCsrfToken() {
        return csrfToken;
    }

    function parseHTML(html) {
        return new DOMParser().parseFromString(html, "text/html");
    }

    async function fetchText(url, options = {}) {
        const headers = {
            "X-Requested-With": "XMLHttpRequest",
            ...(options.headers || {})
        };

        if (csrfToken) {
            headers["X-CSRFToken"] = csrfToken;
        }

        const response = await fetch(url, {
            ...options,
            headers
        });

        if (!response.ok) {
            throw new Error(`Request failed: ${response.status}`);
        }

        return response.text();
    }

    async function fetchJSON(url, options = {}) {
        const headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            ...(options.headers || {})
        };

        if (csrfToken) {
            headers["X-CSRFToken"] = csrfToken;
        }

        const response = await fetch(url, {
            ...options,
            headers
        });

        if (!response.ok) {
            throw new Error(`Request failed: ${response.status}`);
        }

        return response.json();
    }

    function animateBadge(element) {
        element.animate(
            [
                { transform: "scale(1)" },
                { transform: "scale(1.18)" },
                { transform: "scale(1)" }
            ],
            { duration: 220, easing: "ease-out" }
        );
    }

    function ensureBadge(targetId, parentSelector, classes) {
        let badge = document.getElementById(targetId);
        if (badge) {
            return badge;
        }

        const parent = document.querySelector(parentSelector);
        if (!parent) {
            return null;
        }

        badge = document.createElement("span");
        badge.id = targetId;
        badge.className = classes;
        parent.appendChild(badge);
        return badge;
    }

    function updateAlertBadges(count) {
        const numeric = Number(count || 0);

        const headerBadge = ensureBadge(
            "header-alert-badge",
            "a.notification-icon",
            "position-absolute top-0 start-100 translate-middle badge rounded-pill text-bg-danger js-alert-badge"
        );

        const sidebarBadge = ensureBadge(
            "sidebar-alert-badge",
            "a[href='/alerts']",
            "badge text-bg-danger ms-auto js-alert-badge"
        );

        [headerBadge, sidebarBadge].forEach((badge) => {
            if (!badge) {
                return;
            }
            if (numeric > 0) {
                const oldValue = Number(badge.textContent || 0);
                badge.textContent = String(numeric);
                badge.classList.remove("d-none");
                if (oldValue !== numeric) {
                    animateBadge(badge);
                }
            } else {
                badge.textContent = "0";
                badge.classList.add("d-none");
            }
        });
    }

    async function pollAlertBellCount() {
        try {
            const payload = await fetchJSON("/api/v1/alerts/unread-count", { method: "GET" });
            updateAlertBadges(payload.count || 0);
        } catch (error) {
            console.debug("Unread alert poll failed", error);
        }
    }

    function startAlertBellPolling() {
        pollAlertBellCount();
        window.setInterval(pollAlertBellCount, 60000);
    }

    function updateMetricCard(metricName, value) {
        const card = document.querySelector(`[data-metric='${metricName}']`);
        if (!card) {
            return;
        }

        const valueElement = card.querySelector("[data-metric-value]");
        if (!valueElement) {
            return;
        }

        let displayValue = value;
        if (metricName === "otd_rate") {
            displayValue = value === null || value === undefined ? "-" : `${Number(value).toFixed(2)}%`;
        }

        valueElement.textContent = String(displayValue);
        valueElement.classList.remove("skeleton-text");
        card.classList.remove("skeleton-card");

        valueElement.animate(
            [
                { opacity: 0.2, transform: "translateY(4px)" },
                { opacity: 1, transform: "translateY(0)" }
            ],
            { duration: 220, easing: "ease-out" }
        );
    }

    function updateOTDTrend(trend) {
        const trendElement = document.querySelector("[data-otd-trend]");
        if (!trendElement) {
            return;
        }

        trendElement.classList.remove("trend-up", "trend-down", "trend-neutral");
        if (trend === "up") {
            trendElement.classList.add("trend-up");
            trendElement.innerHTML = '<i class="bi bi-arrow-up-right"></i><span>Improving month-over-month</span>';
        } else if (trend === "down") {
            trendElement.classList.add("trend-down");
            trendElement.innerHTML = '<i class="bi bi-arrow-down-right"></i><span>Needs carrier intervention</span>';
        } else {
            trendElement.classList.add("trend-neutral");
            trendElement.innerHTML = '<i class="bi bi-dash"></i><span>Stable trend</span>';
        }
    }

    async function pollDashboardMetrics() {
        const metricsContainer = document.getElementById("metric-cards");
        if (!metricsContainer) {
            return;
        }

        try {
            const metrics = await fetchJSON("/api/v1/dashboard/metrics", { method: "GET" });
            updateMetricCard("active_shipments", metrics.active_shipments);
            updateMetricCard("critical_count", metrics.critical_count);
            updateMetricCard("warning_count", metrics.warning_count);
            updateMetricCard("otd_rate", metrics.otd_rate);
            updateOTDTrend(metrics.otd_trend);
        } catch (error) {
            console.debug("Dashboard metric poll failed", error);
        }
    }

    function startMetricPolling() {
        if (!document.getElementById("metric-cards")) {
            return;
        }

        pollDashboardMetrics();
        window.setInterval(pollDashboardMetrics, 300000);
    }

    function escapeHTML(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function initGlobalSearch() {
        const wrapper = document.querySelector("[data-global-search]");
        const input = wrapper?.querySelector("[data-global-search-input]");
        const resultsPanel = wrapper?.querySelector("[data-global-search-results]");

        if (!wrapper || !input || !resultsPanel) {
            return;
        }

        const searchState = {
            items: [],
            activeIndex: -1
        };

        const groupOrder = [
            { key: "pages", label: "Pages", icon: "bi-compass" },
            { key: "shipments", label: "Shipments", icon: "bi-box-seam" },
            { key: "alerts", label: "Alerts", icon: "bi-bell" },
            { key: "carriers", label: "Carriers", icon: "bi-bar-chart-line" }
        ];

        const closeResults = () => {
            resultsPanel.classList.add("d-none");
            resultsPanel.innerHTML = "";
            searchState.items = [];
            searchState.activeIndex = -1;
        };

        const openResults = () => {
            if (!resultsPanel.innerHTML) {
                return;
            }
            resultsPanel.classList.remove("d-none");
        };

        const renderState = (message, extraClass = "") => {
            resultsPanel.innerHTML = `<div class="global-search-state ${extraClass}">${escapeHTML(message)}</div>`;
            openResults();
        };

        const setActiveIndex = (nextIndex) => {
            if (!searchState.items.length) {
                searchState.activeIndex = -1;
                return;
            }

            const total = searchState.items.length;
            let normalized = nextIndex;
            if (normalized >= total) {
                normalized = 0;
            }
            if (normalized < 0) {
                normalized = total - 1;
            }

            searchState.activeIndex = normalized;
            const items = Array.from(resultsPanel.querySelectorAll(".global-search-item[data-search-index]"));
            items.forEach((node, index) => {
                const active = index === normalized;
                node.classList.toggle("active", active);
                node.setAttribute("aria-selected", active ? "true" : "false");
            });

            const activeNode = resultsPanel.querySelector(`.global-search-item[data-search-index="${normalized}"]`);
            activeNode?.scrollIntoView({ block: "nearest" });
        };

        const renderResults = (payload, query) => {
            const groupedResults = payload?.results || {};
            let html = "";
            const flatItems = [];

            groupOrder.forEach((group) => {
                const items = Array.isArray(groupedResults[group.key]) ? groupedResults[group.key] : [];
                if (!items.length) {
                    return;
                }

                html += `
                    <section class="global-search-group" aria-label="${escapeHTML(group.label)}">
                        <div class="global-search-group-label">
                            <i class="bi ${group.icon}"></i>
                            <span>${escapeHTML(group.label)}</span>
                        </div>
                `;

                items.forEach((item) => {
                    const index = flatItems.length;
                    flatItems.push(item);

                    const title = escapeHTML(item.title || "Untitled");
                    const subtitle = escapeHTML(item.subtitle || "");
                    const url = escapeHTML(item.url || "#");
                    const tags = Array.isArray(item.meta) ? item.meta.filter(Boolean).slice(0, 4) : [];
                    const metaHTML = tags.length
                        ? `<div class="global-search-item-meta">${tags.map((tag) => `<span>${escapeHTML(tag)}</span>`).join("")}</div>`
                        : "";

                    html += `
                        <a href="${url}" class="global-search-item" role="option" data-search-index="${index}" aria-selected="false">
                            <div class="global-search-item-main">
                                <div class="global-search-item-title">${title}</div>
                                ${subtitle ? `<div class="global-search-item-subtitle">${subtitle}</div>` : ""}
                            </div>
                            ${metaHTML}
                        </a>
                    `;
                });

                html += "</section>";
            });

            searchState.items = flatItems;
            searchState.activeIndex = -1;

            if (!flatItems.length) {
                renderState(`No results found for \"${query}\".`);
                return;
            }

            resultsPanel.innerHTML = html;
            openResults();
        };

        const executeSearch = async (query) => {
            if (globalSearchAbortController) {
                globalSearchAbortController.abort();
            }

            const controller = new AbortController();
            globalSearchAbortController = controller;
            renderState("Searching...");

            const url = new URL("/api/v1/search/global", window.location.origin);
            url.searchParams.set("q", query);
            url.searchParams.set("limit", "6");

            try {
                const payload = await fetchJSON(url.toString(), {
                    method: "GET",
                    signal: controller.signal
                });

                if (controller !== globalSearchAbortController) {
                    return;
                }

                if (String(input.value || "").trim() !== query) {
                    return;
                }

                renderResults(payload, query);
            } catch (error) {
                if (error?.name === "AbortError") {
                    return;
                }
                renderState("Search failed. Please try again.", "text-danger");
            }
        };

        input.addEventListener("input", () => {
            const query = String(input.value || "").trim();

            if (globalSearchDebounceHandle) {
                window.clearTimeout(globalSearchDebounceHandle);
            }

            if (query.length < 2) {
                if (globalSearchAbortController) {
                    globalSearchAbortController.abort();
                    globalSearchAbortController = null;
                }
                closeResults();
                return;
            }

            globalSearchDebounceHandle = window.setTimeout(() => {
                executeSearch(query);
            }, 240);
        });

        input.addEventListener("focus", () => {
            const query = String(input.value || "").trim();
            if (query.length >= 2 && resultsPanel.innerHTML) {
                openResults();
            }
        });

        input.addEventListener("keydown", (event) => {
            const panelOpen = !resultsPanel.classList.contains("d-none");
            if (!panelOpen && event.key !== "Enter") {
                return;
            }

            if (event.key === "ArrowDown") {
                event.preventDefault();
                setActiveIndex(searchState.activeIndex + 1);
                return;
            }

            if (event.key === "ArrowUp") {
                event.preventDefault();
                setActiveIndex(searchState.activeIndex < 0 ? searchState.items.length - 1 : searchState.activeIndex - 1);
                return;
            }

            if (event.key === "Escape") {
                event.preventDefault();
                closeResults();
                return;
            }

            if (event.key === "Enter") {
                const activeItem = searchState.items[searchState.activeIndex] || null;
                if (activeItem?.url) {
                    event.preventDefault();
                    window.location.href = activeItem.url;
                    return;
                }

                const query = String(input.value || "").trim();
                if (query.length >= 2) {
                    event.preventDefault();
                    const fallbackURL = new URL("/shipments", window.location.origin);
                    fallbackURL.searchParams.set("q", query);
                    window.location.href = fallbackURL.toString();
                }
            }
        });

        resultsPanel.addEventListener("mousemove", (event) => {
            const target = event.target.closest(".global-search-item[data-search-index]");
            if (!target) {
                return;
            }

            const index = Number(target.getAttribute("data-search-index"));
            if (Number.isInteger(index)) {
                setActiveIndex(index);
            }
        });

        resultsPanel.addEventListener("click", (event) => {
            const target = event.target.closest(".global-search-item[href]");
            if (!target) {
                return;
            }
            closeResults();
        });

        document.addEventListener("click", (event) => {
            if (!wrapper.contains(event.target)) {
                closeResults();
            }
        });
    }

    function getFilterForm() {
        return document.getElementById("shipment-filter-form");
    }

    function setFilterSpinner(visible) {
        const spinner = document.getElementById("filter-loading-spinner");
        if (!spinner) {
            return;
        }
        spinner.classList.toggle("d-none", !visible);
    }

    function setTableLoading(loading) {
        const tableCard = document.getElementById("shipment-table-card");
        if (!tableCard) {
            return;
        }
        tableCard.classList.toggle("loading", loading);
    }

    function applySortUIFromURL(url) {
        const current = new URL(url, window.location.origin);
        const sort = current.searchParams.get("sort") || "";
        const order = current.searchParams.get("order") || "";

        document.querySelectorAll("th.sortable").forEach((th) => {
            th.classList.remove("active", "sort-asc", "sort-desc");
            if (th.dataset.sort === sort) {
                th.classList.add("active", order === "asc" ? "sort-asc" : "sort-desc");
            }
        });

        const form = getFilterForm();
        if (form) {
            const sortInput = form.querySelector("input[name='sort']");
            const orderInput = form.querySelector("input[name='order']");
            if (sortInput) {
                sortInput.value = sort;
            }
            if (orderInput) {
                orderInput.value = order;
            }
        }
    }

    function updateActiveFilterBadge() {
        const form = getFilterForm();
        const badge = document.querySelector("[data-active-filter-badge]");
        if (!form || !badge) {
            return;
        }

        const trackedNames = ["q", "status", "carrier_id", "mode", "risk", "risk_level"];
        let count = 0;

        trackedNames.forEach((name) => {
            const field = form.elements.namedItem(name);
            if (!field) {
                return;
            }
            const value = String(field.value || "").trim();
            if (value) {
                count += 1;
            }
        });

        if (count > 0) {
            badge.textContent = `${count} active`;
            badge.classList.remove("d-none");
        } else {
            badge.classList.add("d-none");
        }
    }

    function bindRowSelectionHandlers() {
        const checkboxes = Array.from(document.querySelectorAll(".row-select-checkbox"));
        if (!checkboxes.length) {
            return;
        }

        const selectAll = document.getElementById("select-all-rows");
        const toolbar = document.getElementById("bulk-toolbar");
        const countElement = document.querySelector("[data-selected-count]");

        const refreshSelectionUI = () => {
            const selected = checkboxes.filter((item) => item.checked);

            checkboxes.forEach((checkbox) => {
                const row = checkbox.closest("tr");
                if (!row) {
                    return;
                }
                row.classList.toggle("selected", checkbox.checked);
            });

            if (countElement) {
                countElement.textContent = String(selected.length);
            }

            if (toolbar) {
                toolbar.classList.toggle("active", selected.length > 0);
            }

            if (selectAll) {
                selectAll.checked = selected.length > 0 && selected.length === checkboxes.length;
            }
        };

        if (selectAll) {
            selectAll.addEventListener("change", () => {
                checkboxes.forEach((checkbox) => {
                    checkbox.checked = selectAll.checked;
                });
                refreshSelectionUI();
            });
        }

        checkboxes.forEach((checkbox) => {
            checkbox.addEventListener("change", refreshSelectionUI);
        });

        const deselectButton = document.getElementById("bulk-deselect-all");
        if (deselectButton) {
            deselectButton.addEventListener("click", () => {
                checkboxes.forEach((checkbox) => {
                    checkbox.checked = false;
                });
                refreshSelectionUI();
            });
        }

        const exportSelectedButton = document.getElementById("bulk-export-selected");
        if (exportSelectedButton) {
            exportSelectedButton.addEventListener("click", () => {
                const selectedIds = checkboxes.filter((item) => item.checked).map((item) => item.value);
                if (!selectedIds.length) {
                    return;
                }

                const form = document.createElement("form");
                form.method = "POST";
                form.action = "/shipments";

                const csrfInput = document.createElement("input");
                csrfInput.type = "hidden";
                csrfInput.name = "csrf_token";
                csrfInput.value = getCsrfToken();
                form.appendChild(csrfInput);

                const exportInput = document.createElement("input");
                exportInput.type = "hidden";
                exportInput.name = "export";
                exportInput.value = "csv";
                form.appendChild(exportInput);

                const selectedInput = document.createElement("input");
                selectedInput.type = "hidden";
                selectedInput.name = "selected_ids";
                selectedInput.value = selectedIds.join(",");
                form.appendChild(selectedInput);

                const filterForm = getFilterForm();
                if (filterForm) {
                    ["q", "status", "carrier_id", "mode", "risk", "sort", "order"].forEach((fieldName) => {
                        const field = filterForm.elements.namedItem(fieldName);
                        if (!field || !field.value) {
                            return;
                        }
                        const hidden = document.createElement("input");
                        hidden.type = "hidden";
                        hidden.name = fieldName;
                        hidden.value = field.value;
                        form.appendChild(hidden);
                    });
                }

                document.body.appendChild(form);
                form.submit();
                form.remove();
            });
        }

        refreshSelectionUI();
    }

    function bindShipmentRowNavigation() {
        const ignoreSelector = "a, button, input, select, textarea, label, .dropdown-menu, [data-bs-toggle]";

        document.querySelectorAll(".shipment-row[data-row-href]").forEach((row) => {
            const targetUrl = row.getAttribute("data-row-href");
            if (!targetUrl || row.dataset.rowNavBound === "true") {
                return;
            }

            const navigate = () => {
                window.location.href = targetUrl;
            };

            row.addEventListener("click", (event) => {
                if (event.target.closest(ignoreSelector)) {
                    return;
                }
                navigate();
            });

            row.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") {
                    return;
                }
                if (event.target.closest(ignoreSelector)) {
                    return;
                }
                event.preventDefault();
                navigate();
            });

            row.dataset.rowNavBound = "true";
        });

        document.querySelectorAll(".shipment-card[data-card-href]").forEach((card) => {
            const targetUrl = card.getAttribute("data-card-href");
            if (!targetUrl || card.dataset.cardNavBound === "true") {
                return;
            }

            const navigate = () => {
                window.location.href = targetUrl;
            };

            card.addEventListener("click", (event) => {
                if (event.target.closest(ignoreSelector)) {
                    return;
                }
                navigate();
            });

            card.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") {
                    return;
                }
                if (event.target.closest(ignoreSelector)) {
                    return;
                }
                event.preventDefault();
                navigate();
            });

            card.dataset.cardNavBound = "true";
        });
    }

    function bindArchiveActions() {
        let pendingArchiveUrl = "";

        async function archiveShipment(url, redirectToList = false) {
            try {
                const payload = await fetchJSON(url, { method: "POST" });
                if (!payload.success) {
                    return;
                }
                if (redirectToList) {
                    window.location.href = "/shipments";
                    return;
                }

                const activePath = window.location.pathname;
                if (activePath.startsWith("/shipments/") && activePath.split("/").length > 3) {
                    window.location.href = "/shipments";
                } else {
                    const sourceUrl = `${window.location.pathname}${window.location.search}`;
                    refreshShipmentList(sourceUrl, false);
                }
            } catch (error) {
                console.error("Archive failed", error);
            }
        }

        const archiveButton = document.getElementById("archive-shipment-btn");
        if (archiveButton) {
            archiveButton.addEventListener("click", () => {
                pendingArchiveUrl = archiveButton.dataset.archiveUrl || "";
                const modalElement = document.getElementById("archiveShipmentModal");
                if (!modalElement || !window.bootstrap) {
                    if (pendingArchiveUrl && window.confirm("Archive this shipment?")) {
                        archiveShipment(pendingArchiveUrl, true);
                    }
                    return;
                }
                const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
                modal.show();
            });
        }

        const confirmArchiveButton = document.getElementById("confirm-archive-shipment");
        if (confirmArchiveButton) {
            confirmArchiveButton.addEventListener("click", () => {
                if (!pendingArchiveUrl) {
                    return;
                }
                archiveShipment(pendingArchiveUrl, true);
                const modalElement = document.getElementById("archiveShipmentModal");
                if (modalElement && window.bootstrap) {
                    const modal = bootstrap.Modal.getInstance(modalElement);
                    if (modal) {
                        modal.hide();
                    }
                }
            });
        }

        document.querySelectorAll(".js-archive-shipment").forEach((button) => {
            button.addEventListener("click", () => {
                const archiveUrl = button.dataset.archiveUrl;
                const reference = button.dataset.shipmentReference || "this shipment";
                if (!archiveUrl) {
                    return;
                }
                if (window.confirm(`Archive ${reference}? It will be hidden from active views.`)) {
                    archiveShipment(archiveUrl, false);
                }
            });
        });
    }

    function bindDashboardRecentAlertLinks() {
        document.querySelectorAll(".dashboard-alert-link[data-nav-url]").forEach((node) => {
            const navigate = () => {
                const targetUrl = node.getAttribute("data-nav-url");
                if (!targetUrl) {
                    return;
                }
                window.location.href = targetUrl;
            };

            node.addEventListener("click", (event) => {
                if (event.target.closest("a")) {
                    return;
                }
                navigate();
            });

            node.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    navigate();
                }
            });
        });
    }

    function bindRouteDecisionForms() {
        document.querySelectorAll(".route-decision-form").forEach((form) => {
            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                const submitter = event.submitter;
                if (!submitter) {
                    return;
                }

                const approveUrl = form.dataset.approveUrl;
                const dismissUrl = form.dataset.dismissUrl;
                const endpoint = submitter.name === "submit_dismiss" ? dismissUrl : approveUrl;
                if (!endpoint) {
                    return;
                }

                const feedback = form.querySelector(".route-decision-feedback");
                const buttons = Array.from(form.querySelectorAll("button"));
                buttons.forEach((button) => {
                    button.disabled = true;
                });

                const originalLabel = submitter.innerHTML;
                submitter.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving';

                const formData = new FormData(form);
                formData.append(submitter.name, submitter.value || "1");

                try {
                    const response = await fetch(endpoint, {
                        method: "POST",
                        headers: {
                            "X-CSRFToken": getCsrfToken(),
                            "X-Requested-With": "XMLHttpRequest"
                        },
                        body: formData
                    });

                    const payload = await response.json();
                    if (!response.ok || !payload.success) {
                        throw new Error(payload.message || "Could not record decision.");
                    }

                    const card = form.closest(".route-alt-card");
                    if (feedback) {
                        feedback.classList.remove("d-none", "text-danger");
                        feedback.classList.add("text-success");
                    }

                    if (submitter.name === "submit_dismiss") {
                        if (feedback) {
                            feedback.textContent = "Decision recorded: Recommendation dismissed.";
                        }
                    } else {
                        const name = payload.decided_by_name || "Team";
                        const decidedAt = payload.decided_at
                            ? new Date(payload.decided_at).toLocaleString()
                            : "now";
                        if (feedback) {
                            feedback.textContent = `Decision recorded: Approved by ${name} at ${decidedAt}.`;
                        }
                    }

                    if (card) {
                        card.classList.add("opacity-75");
                    }

                    const textareas = Array.from(form.querySelectorAll("textarea, input"));
                    textareas.forEach((node) => {
                        node.disabled = true;
                    });
                } catch (error) {
                    if (feedback) {
                        feedback.classList.remove("d-none", "text-success");
                        feedback.classList.add("text-danger");
                        feedback.textContent = error.message || "Failed to submit decision.";
                    }
                    buttons.forEach((button) => {
                        button.disabled = false;
                    });
                } finally {
                    submitter.innerHTML = originalLabel;
                }
            });
        });
    }

    function bindCharacterCounters() {
        const update = (textarea) => {
            const maxLength = Number(textarea.dataset.maxLength || textarea.getAttribute("maxlength") || 0);
            if (!maxLength) {
                return;
            }

            let counter = document.querySelector(`.textarea-char-counter[data-for='${textarea.id}']`);
            if (!counter) {
                counter = document.createElement("small");
                counter.className = "textarea-char-counter text-muted";
                counter.dataset.for = textarea.id;
                textarea.insertAdjacentElement("afterend", counter);
            }

            const currentLength = textarea.value.length;
            counter.textContent = `${currentLength} / ${maxLength} characters`;
            counter.classList.remove("text-muted", "text-warning", "text-danger");
            if (currentLength >= maxLength) {
                counter.classList.add("text-danger");
            } else if (currentLength >= maxLength * 0.85) {
                counter.classList.add("text-warning");
            } else {
                counter.classList.add("text-muted");
            }
        };

        document.querySelectorAll("textarea[data-max-length]").forEach((textarea) => {
            update(textarea);
            textarea.addEventListener("input", () => update(textarea));
        });
    }

    function startExecutionCountdowns() {
        document.querySelectorAll(".execution-countdown[data-deadline]").forEach((element) => {
            const deadline = new Date(element.dataset.deadline);
            if (Number.isNaN(deadline.getTime())) {
                return;
            }

            const display = element.querySelector("span") || element;
            const card = element.closest(".route-alt-card");
            const approveButton = card?.querySelector(".btn-approve-route") || null;

            const tick = () => {
                const remainingMs = deadline.getTime() - Date.now();
                if (remainingMs <= 0) {
                    display.textContent = "Expired";
                    element.classList.add("urgent");
                    if (approveButton) {
                        approveButton.disabled = true;
                    }
                    return true;
                }

                const remainingSeconds = Math.floor(remainingMs / 1000);
                const days = Math.floor(remainingSeconds / 86400);
                const hours = Math.floor((remainingSeconds % 86400) / 3600);
                const minutes = Math.floor((remainingSeconds % 3600) / 60);
                const seconds = remainingSeconds % 60;

                display.textContent = `${days}d ${hours}h ${minutes}m ${seconds}s remaining`;
                if (remainingSeconds < 86400) {
                    element.classList.add("urgent");
                }
                return false;
            };

            if (tick()) {
                return;
            }

            const intervalId = window.setInterval(() => {
                if (tick()) {
                    window.clearInterval(intervalId);
                }
            }, 1000);
        });
    }

    function initTooltips() {
        if (!window.bootstrap) {
            return;
        }
        document.querySelectorAll(".drs-sub-score-info-icon").forEach((element) => {
            bootstrap.Tooltip.getOrCreateInstance(element);
        });
    }

    function bindDateValidation() {
        document.querySelectorAll("form[data-date-validation='true']").forEach((form) => {
            const departure = form.querySelector("input[name='estimated_departure']");
            const arrival = form.querySelector("input[name='estimated_arrival']");
            const errorBox = document.getElementById("shipment-date-error");
            if (!departure || !arrival || !errorBox) {
                return;
            }

            const validate = () => {
                const departureValue = departure.value ? new Date(departure.value) : null;
                const arrivalValue = arrival.value ? new Date(arrival.value) : null;
                const valid = !(departureValue && arrivalValue && arrivalValue <= departureValue);

                if (!valid) {
                    errorBox.textContent = "Estimated arrival must be after estimated departure.";
                    errorBox.classList.remove("d-none");
                } else {
                    errorBox.classList.add("d-none");
                }
                return valid;
            };

            [departure, arrival].forEach((field) => {
                field.addEventListener("input", validate);
                field.addEventListener("change", validate);
            });

            form.addEventListener("submit", (event) => {
                if (!validate()) {
                    event.preventDefault();
                }
            });
        });
    }

    function bindCSVDropzone() {
        const dropzone = document.getElementById("csv-dropzone");
        const fileInput = document.getElementById("csv-file-input");
        const fileNameLabel = document.getElementById("csv-selected-filename");

        if (!dropzone || !fileInput || !fileNameLabel) {
            return;
        }

        const updateFileName = () => {
            const file = fileInput.files && fileInput.files[0];
            fileNameLabel.textContent = file ? file.name : "No file selected";
        };

        ["dragenter", "dragover"].forEach((eventName) => {
            dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                dropzone.classList.add("drag-over");
            });
        });

        ["dragleave", "drop"].forEach((eventName) => {
            dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                dropzone.classList.remove("drag-over");
            });
        });

        dropzone.addEventListener("drop", (event) => {
            const files = event.dataTransfer?.files;
            if (files && files.length > 0) {
                fileInput.files = files;
                updateFileName();
            }
        });

        fileInput.addEventListener("change", updateFileName);
    }

    function buildURLFromFilterForm(targetPath) {
        const form = getFilterForm();
        const url = new URL(targetPath || window.location.pathname, window.location.origin);
        if (!form) {
            return url;
        }

        const params = new URLSearchParams(new FormData(form));
        params.forEach((value, key) => {
            if (value !== "") {
                url.searchParams.set(key, value);
            }
        });
        return url;
    }

    function bindExportCSVButton() {
        const exportButton = document.getElementById("export-csv-btn");
        if (!exportButton) {
            return;
        }

        exportButton.addEventListener("click", () => {
            const isDashboard = window.location.pathname.startsWith("/dashboard");
            const url = buildURLFromFilterForm(isDashboard ? "/shipments" : "/shipments");
            url.searchParams.set("export", "csv");
            window.location.href = url.toString();
        });
    }

    async function refreshShipmentList(url, pushState = true) {
        const body = document.getElementById("shipment-table-body");
        if (!body) {
            return;
        }

        setTableLoading(true);
        setFilterSpinner(true);

        try {
            const html = await fetchText(url, { method: "GET" });
            const newDoc = parseHTML(html);

            const replacementPairs = [
                ["shipment-table-body", "innerHTML"],
                ["shipment-card-stack", "innerHTML"],
                ["shipments-pagination", "innerHTML"],
                ["metric-cards", "innerHTML"],
                ["recent-alert-list", "innerHTML"],
                ["recent-alert-count", "textContent"]
            ];

            replacementPairs.forEach(([id, strategy]) => {
                const currentNode = document.getElementById(id);
                const incomingNode = newDoc.getElementById(id);
                if (!currentNode || !incomingNode) {
                    return;
                }

                if (strategy === "textContent") {
                    currentNode.textContent = incomingNode.textContent;
                } else {
                    currentNode.innerHTML = incomingNode.innerHTML;
                }
            });

            if (pushState) {
                history.pushState({}, "", url);
            }

            applySortUIFromURL(url);
            updateActiveFilterBadge();
            bindRowSelectionHandlers();
            bindShipmentRowNavigation();
            bindArchiveActions();
            bindEmptyStateClearButtons();
            bindDashboardRecentAlertLinks();
        } catch (error) {
            console.error("Failed to refresh shipment table", error);
        } finally {
            setTableLoading(false);
            setFilterSpinner(false);
        }
    }

    function bindSortableHeaders() {
        const sortHeaders = document.querySelectorAll("th.sortable");
        if (!sortHeaders.length) {
            return;
        }

        sortHeaders.forEach((header) => {
            header.addEventListener("click", () => {
                const sort = header.dataset.sort;
                if (!sort) {
                    return;
                }

                const currentUrl = new URL(window.location.href);
                const currentSort = currentUrl.searchParams.get("sort") || "disruption_risk_score";
                const currentOrder = currentUrl.searchParams.get("order") || "desc";

                const nextOrder = currentSort === sort && currentOrder === "asc" ? "desc" : "asc";

                currentUrl.searchParams.set("sort", sort);
                currentUrl.searchParams.set("order", nextOrder);
                currentUrl.searchParams.delete("page");

                const form = getFilterForm();
                if (form) {
                    const sortInput = form.querySelector("input[name='sort']");
                    const orderInput = form.querySelector("input[name='order']");
                    if (sortInput) {
                        sortInput.value = sort;
                    }
                    if (orderInput) {
                        orderInput.value = nextOrder;
                    }
                }

                refreshShipmentList(currentUrl.toString(), true);
            });
        });

        applySortUIFromURL(window.location.href);
    }

    function bindEmptyStateClearButtons() {
        ["empty-clear-filters", "empty-clear-filters-mobile"].forEach((id) => {
            const button = document.getElementById(id);
            if (!button) {
                return;
            }
            button.addEventListener("click", () => {
                clearFilters();
            });
        });
    }

    function clearFilters() {
        const form = getFilterForm();
        if (!form) {
            return;
        }

        ["q", "status", "carrier_id", "mode", "risk", "risk_level"].forEach((name) => {
            const field = form.elements.namedItem(name);
            if (field) {
                field.value = "";
            }
        });

        const sortInput = form.querySelector("input[name='sort']");
        const orderInput = form.querySelector("input[name='order']");
        if (sortInput && !sortInput.value) {
            sortInput.value = "disruption_risk_score";
        }
        if (orderInput && !orderInput.value) {
            orderInput.value = "desc";
        }

        const targetPath = window.location.pathname.startsWith("/dashboard") ? "/dashboard" : "/shipments";
        const nextUrl = new URL(targetPath, window.location.origin);
        if (sortInput && sortInput.value) {
            nextUrl.searchParams.set("sort", sortInput.value);
        }
        if (orderInput && orderInput.value) {
            nextUrl.searchParams.set("order", orderInput.value);
        }

        refreshShipmentList(nextUrl.toString(), true);
    }

    function bindFilterAutoSubmit() {
        const form = getFilterForm();
        if (!form) {
            return;
        }

        const triggerRefresh = () => {
            if (filterDebounceHandle) {
                window.clearTimeout(filterDebounceHandle);
            }

            filterDebounceHandle = window.setTimeout(() => {
                const currentUrl = buildURLFromFilterForm(window.location.pathname);
                currentUrl.searchParams.delete("page");
                refreshShipmentList(currentUrl.toString(), true);
            }, 500);
        };

        form.querySelectorAll("[data-filter-input]").forEach((field) => {
            field.addEventListener("input", triggerRefresh);
            field.addEventListener("change", triggerRefresh);
        });

        const clearButton = document.getElementById("clear-shipment-filters");
        if (clearButton) {
            clearButton.addEventListener("click", clearFilters);
        }

        updateActiveFilterBadge();
    }

    function bindHistoryPopstate() {
        window.addEventListener("popstate", () => {
            if (!document.getElementById("shipment-table-body")) {
                return;
            }
            refreshShipmentList(window.location.href, false);
        });
    }

    function initShipmentPages() {
        if (!document.getElementById("shipment-table-body") && !document.getElementById("shipment-card-stack")) {
            return;
        }

        bindSortableHeaders();
        bindFilterAutoSubmit();
        bindRowSelectionHandlers();
        bindShipmentRowNavigation();
        bindExportCSVButton();
        bindArchiveActions();
        bindHistoryPopstate();
        bindEmptyStateClearButtons();
        bindDashboardRecentAlertLinks();
    }

    function init() {
        startAlertBellPolling();
        startMetricPolling();
        initGlobalSearch();
        initShipmentPages();
        bindRouteDecisionForms();
        bindCharacterCounters();
        startExecutionCountdowns();
        initTooltips();
        bindDateValidation();
        bindCSVDropzone();
        bindArchiveActions();
        bindExportCSVButton();
        bindDashboardRecentAlertLinks();
    }

    return {
        init,
        updateAlertBadges,
        refreshShipmentList,
        getCsrfToken
    };
})();

document.addEventListener("DOMContentLoaded", () => {
    ChainWatchDashboard.init();
});

export default ChainWatchDashboard;
