(() => {
    const qs = (selector, parent = document) => parent.querySelector(selector);
    const qsa = (selector, parent = document) => Array.from(parent.querySelectorAll(selector));

    // Smooth anchor scrolling on landing page links
    qsa('a[href^="#"]').forEach((anchor) => {
        anchor.addEventListener("click", (event) => {
            const targetId = anchor.getAttribute("href");
            if (!targetId || targetId === "#") {
                return;
            }
            const target = qs(targetId);
            if (!target) {
                return;
            }
            event.preventDefault();
            target.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    });

    // Auto-dismiss flash alerts after five seconds
    qsa(".alert[data-auto-dismiss]").forEach((alertEl) => {
        const delay = Number(alertEl.getAttribute("data-auto-dismiss") || 5000);
        window.setTimeout(() => {
            if (alertEl && window.bootstrap) {
                const instance = bootstrap.Alert.getOrCreateInstance(alertEl);
                instance.close();
            }
        }, delay);
    });

    // Metrics counter animation
    const metricsStrip = qs(".metrics-strip");
    if (metricsStrip) {
        const metrics = qsa(".metric-number", metricsStrip);
        let hasAnimated = false;

        const animateCounter = (element) => {
            const targetText = element.getAttribute("data-target") || "0";
            const suffix = targetText.endsWith("+")
                ? "+"
                : targetText.endsWith("%")
                    ? "%"
                    : "";
            const numeric = Number(targetText.replace(/[+%]/g, "").replace(/[^0-9.-]/g, "")) || 0;
            const duration = 2000;
            const start = performance.now();

            const frame = (now) => {
                const elapsed = Math.min(now - start, duration);
                const progress = elapsed / duration;
                const eased = 1 - Math.pow(1 - progress, 3);
                const current = Math.floor(eased * numeric);
                element.textContent = `${current}${suffix}`;
                if (elapsed < duration) {
                    requestAnimationFrame(frame);
                } else {
                    element.textContent = `${numeric}${suffix}`;
                }
            };

            requestAnimationFrame(frame);
        };

        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting && !hasAnimated) {
                        metrics.forEach(animateCounter);
                        hasAnimated = true;
                    }
                });
            },
            { threshold: 0.35 }
        );

        observer.observe(metricsStrip);
    }

    // Generic pricing toggle across homepage and pricing page
    qsa("[data-plan-toggle]").forEach((toggleWrap) => {
        const sectionSelector = toggleWrap.getAttribute("data-target-section") || "#pricingSection";
        const targetSection = qs(sectionSelector);
        const monthlyBtn = qs('[data-plan-value="monthly"]', toggleWrap);
        const annualBtn = qs('[data-plan-value="annual"]', toggleWrap);

        if (!targetSection || !monthlyBtn || !annualBtn) {
            return;
        }

        const setMode = (mode) => {
            const annual = mode === "annual";
            targetSection.classList.toggle("annual-active", annual);
            monthlyBtn.classList.toggle("active", !annual);
            annualBtn.classList.toggle("active", annual);
        };

        monthlyBtn.addEventListener("click", () => setMode("monthly"));
        annualBtn.addEventListener("click", () => setMode("annual"));
    });

    // Blog category filter
    const blogFilterContainer = qs("[data-blog-filter]");
    if (blogFilterContainer) {
        const tabs = qsa(".category-pill", blogFilterContainer);
        const cards = qsa(".post-card[data-category]");

        tabs.forEach((tab) => {
            tab.addEventListener("click", () => {
                const category = tab.getAttribute("data-category");
                tabs.forEach((item) => item.classList.remove("active"));
                tab.classList.add("active");

                cards.forEach((card) => {
                    const cardCategory = card.getAttribute("data-category");
                    const shouldShow = category === "All" || cardCategory === category;
                    card.closest("[data-post-wrapper]")?.classList.toggle("d-none", !shouldShow);
                });
            });
        });
    }

    // Newsletter fake subscribe
    const newsletterForm = qs("[data-newsletter-form]");
    if (newsletterForm) {
        newsletterForm.addEventListener("submit", (event) => {
            event.preventDefault();
            window.alert("Thanks for subscribing! We'll be in touch soon.");
            newsletterForm.reset();
        });
    }

    // Onboarding step 2 setup method toggle
    const setupMethodWrap = qs("[data-setup-method-wrap]");
    if (setupMethodWrap) {
        const cards = qsa(".setup-method-card", setupMethodWrap);
        const csvSection = qs("[data-csv-section]");
        const carrierSection = qs("[data-carrier-section]");

        const updateSetupVisibility = () => {
            const checked = qs('input[name="setup_method"]:checked');
            const method = checked ? checked.value : "manual";

            if (csvSection) {
                csvSection.classList.toggle("d-none", method !== "csv");
            }
            if (carrierSection) {
                carrierSection.classList.toggle("d-none", method !== "manual");
            }

            cards.forEach((card) => {
                const cardValue = card.getAttribute("data-method");
                card.classList.toggle("active", cardValue === method);
            });
        };

        cards.forEach((card) => {
            card.addEventListener("click", () => {
                const value = card.getAttribute("data-method");
                const input = qs(`input[name="setup_method"][value="${value}"]`);
                if (input) {
                    input.checked = true;
                    updateSetupVisibility();
                }
            });
        });

        qsa('input[name="setup_method"]').forEach((radio) => {
            radio.addEventListener("change", updateSetupVisibility);
        });

        updateSetupVisibility();
    }

    // Onboarding step 2 quick select carrier by mode
    qsa("[data-select-mode]").forEach((btn) => {
        btn.addEventListener("click", (event) => {
            event.preventDefault();
            const mode = btn.getAttribute("data-select-mode");
            qsa('[data-carrier-mode]').forEach((carrierCard) => {
                const matches = carrierCard.getAttribute("data-carrier-mode") === mode;
                const checkbox = qs('input[type="checkbox"]', carrierCard);
                if (checkbox && matches) {
                    checkbox.checked = true;
                }
            });
        });
    });

    // Onboarding threshold sliders and validation
    const warningInput = qs('[data-threshold-input="warning"]');
    const criticalInput = qs('[data-threshold-input="critical"]');
    const warningValue = qs('[data-threshold-value="warning"]');
    const criticalValue = qs('[data-threshold-value="critical"]');
    const warningMarker = qs('[data-threshold-marker="warning"]');
    const criticalMarker = qs('[data-threshold-marker="critical"]');
    const thresholdError = qs("[data-threshold-error]");
    const thresholdSubmitBtn = qs('[data-threshold-submit]');

    const updateThreshold = () => {
        if (!warningInput || !criticalInput) {
            return;
        }

        const warning = Number(warningInput.value || 0);
        const critical = Number(criticalInput.value || 0);

        if (warningValue) {
            warningValue.textContent = String(warning);
        }
        if (criticalValue) {
            criticalValue.textContent = String(critical);
        }

        const warningPercent = Math.min(100, Math.max(0, warning));
        const criticalPercent = Math.min(100, Math.max(0, critical));

        if (warningMarker) {
            warningMarker.style.left = `${warningPercent}%`;
        }
        if (criticalMarker) {
            criticalMarker.style.left = `${criticalPercent}%`;
        }

        const isValid = critical > warning;
        if (thresholdError) {
            thresholdError.classList.toggle("d-none", isValid);
        }
        if (thresholdSubmitBtn) {
            thresholdSubmitBtn.disabled = !isValid;
        }
    };

    if (warningInput && criticalInput) {
        warningInput.addEventListener("input", updateThreshold);
        criticalInput.addEventListener("input", updateThreshold);
        updateThreshold();
    }

    // Step 3 team invite dynamic fields
    const addInviteBtn = qs("[data-add-invite]");
    if (addInviteBtn) {
        const rows = qsa("[data-invite-row]");

        const updateInviteUI = () => {
            const hiddenRows = rows.filter((row) => row.classList.contains("d-none"));
            addInviteBtn.classList.toggle("d-none", hiddenRows.length === 0);
        };

        addInviteBtn.addEventListener("click", (event) => {
            event.preventDefault();
            const nextHidden = rows.find((row) => row.classList.contains("d-none"));
            if (nextHidden) {
                nextHidden.classList.remove("d-none");
            }
            updateInviteUI();
        });

        updateInviteUI();
    }

    // Step 4 dashboard mini preview
    const previewWrap = qs("[data-dashboard-preview]");
    if (previewWrap) {
        qsa('[data-preview-toggle]').forEach((input) => {
            const syncVisibility = () => {
                const target = input.getAttribute("data-preview-toggle");
                const card = qs(`[data-preview-card="${target}"]`, previewWrap);
                if (!card) {
                    return;
                }
                card.classList.toggle("d-none", !input.checked);
            };

            input.addEventListener("change", syncVisibility);
            syncVisibility();
        });
    }

    // Dynamic TOC on blog post pages
    const articleContent = qs("[data-article-content]");
    const tocList = qs("[data-article-toc-list]");
    if (articleContent && tocList) {
        const headings = qsa("h2, h3", articleContent);
        headings.forEach((heading, index) => {
            if (!heading.id) {
                heading.id = `section-${index + 1}`;
            }
            const li = document.createElement("li");
            li.className = heading.tagName.toLowerCase() === "h3" ? "ps-3" : "";
            const a = document.createElement("a");
            a.href = `#${heading.id}`;
            a.textContent = heading.textContent || `Section ${index + 1}`;
            li.appendChild(a);
            tocList.appendChild(li);
        });

        const tocLinks = qsa("a", tocList);
        const tocObserver = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting) {
                        tocLinks.forEach((link) => {
                            link.classList.toggle(
                                "active",
                                link.getAttribute("href") === `#${entry.target.id}`
                            );
                        });
                    }
                });
            },
            { rootMargin: "-40% 0px -55% 0px", threshold: 0.01 }
        );

        headings.forEach((heading) => tocObserver.observe(heading));
    }

    // Scroll reveal animation
    const revealEls = qsa(".fade-in-up");
    if (revealEls.length > 0) {
        const revealObserver = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting) {
                        entry.target.classList.add("visible");
                        revealObserver.unobserve(entry.target);
                    }
                });
            },
            { threshold: 0.15 }
        );

        revealEls.forEach((el) => revealObserver.observe(el));
    }
})();
