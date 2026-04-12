const chartInitState = {
    loaded: false,
    loadingPromise: null
};

function loadScript(src) {
    return new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[src="${src}"]`);
        if (existing) {
            if (window.Chart || existing.dataset.loaded === "true" || existing.readyState === "complete") {
                resolve();
                return;
            }
            existing.addEventListener("load", () => resolve(), { once: true });
            existing.addEventListener("error", () => reject(new Error(`Failed loading ${src}`)), { once: true });
            return;
        }

        const script = document.createElement("script");
        script.src = src;
        script.async = true;
        script.dataset.loaded = "false";
        script.addEventListener("load", () => {
            script.dataset.loaded = "true";
            resolve();
        });
        script.addEventListener("error", () => reject(new Error(`Failed loading ${src}`)));
        document.head.appendChild(script);
    });
}

async function ensureChartJsLoaded() {
    if (window.Chart) {
        chartInitState.loaded = true;
        return;
    }

    if (chartInitState.loadingPromise) {
        await chartInitState.loadingPromise;
        return;
    }

    chartInitState.loadingPromise = (async () => {
        await loadScript("https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js");
    })();

    await chartInitState.loadingPromise;
    chartInitState.loaded = true;
}

function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

export function hexToRgba(hex, alpha = 1) {
    const clean = String(hex || "").replace("#", "").trim();
    if (clean.length !== 6) {
        return `rgba(0, 0, 0, ${alpha})`;
    }
    const num = Number.parseInt(clean, 16);
    const r = (num >> 16) & 255;
    const g = (num >> 8) & 255;
    const b = num & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function getChartColors() {
    return {
        navy: cssVar("--color-navy", "#1B3A6B"),
        actionBlue: cssVar("--color-action-blue", "#0077CC"),
        success: cssVar("--color-success", "#00A86B"),
        warningOrange: cssVar("--color-warning-orange", "#FF8C00"),
        criticalRed: cssVar("--color-critical-red", "#D32F2F"),
        watchAmber: cssVar("--color-watch-amber", "#F59E0B"),
        lightBlue: cssVar("--color-light-blue", "#EBF4FF"),
        neutralGray: cssVar("--color-neutral-gray", "#F5F7FA"),
        textDark: cssVar("--color-text-dark", "#1A1A2E"),
        textMuted: cssVar("--color-text-muted", "#555577")
    };
}

function normalizeRate(value) {
    const numeric = Number(value || 0);
    return numeric <= 1 ? numeric * 100 : numeric;
}

export function destroyChart(canvasId) {
    if (!window.Chart) {
        return;
    }
    const existing = window.Chart.getChart(canvasId);
    if (existing) {
        existing.destroy();
    }
}

function buildCommonTooltipColors() {
    const colors = getChartColors();
    return {
        backgroundColor: hexToRgba(colors.navy, 0.95),
        titleColor: "#ffffff",
        bodyColor: "#ffffff",
        borderColor: hexToRgba(colors.actionBlue, 0.45),
        borderWidth: 1,
        padding: 10,
        cornerRadius: 8
    };
}

function withChart(canvasId, buildFn) {
    return ensureChartJsLoaded().then(() => {
        destroyChart(canvasId);
        const canvas = typeof canvasId === "string" ? document.getElementById(canvasId) : canvasId;
        if (!canvas) {
            return null;
        }
        return buildFn(canvas);
    });
}

export function initOTDTrendChart(canvasId, chartData = []) {
    return withChart(canvasId, (canvas) => {
        const colors = getChartColors();
        const labels = chartData.map((row) => row.period || "-");
        const values = chartData.map((row) => Number(normalizeRate(row.otd_rate || 0).toFixed(2)));
        const benchmarkValues = labels.map(() => 80);

        return new window.Chart(canvas, {
            type: "line",
            data: {
                labels,
                datasets: [
                    {
                        label: "OTD Rate (%)",
                        data: values,
                        borderColor: colors.actionBlue,
                        backgroundColor: (context) => {
                            const chart = context.chart;
                            const { ctx, chartArea } = chart;
                            if (!chartArea) {
                                return hexToRgba(colors.actionBlue, 0.15);
                            }
                            const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                            gradient.addColorStop(0, hexToRgba(colors.actionBlue, 0.25));
                            gradient.addColorStop(1, hexToRgba(colors.actionBlue, 0.03));
                            return gradient;
                        },
                        fill: true,
                        tension: 0.3,
                        pointRadius: 4,
                        pointHoverRadius: 7,
                        pointBackgroundColor: colors.actionBlue
                    },
                    {
                        label: "Industry Benchmark (80%)",
                        data: benchmarkValues,
                        borderColor: hexToRgba(colors.textMuted, 0.9),
                        borderDash: [6, 4],
                        pointRadius: 0,
                        fill: false,
                        tension: 0
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                scales: {
                    x: {
                        grid: { color: hexToRgba(colors.textMuted, 0.08) },
                        ticks: { color: colors.textMuted }
                    },
                    y: {
                        min: 0,
                        max: 100,
                        ticks: {
                            color: colors.textMuted,
                            callback: (value) => `${value}%`
                        },
                        grid: { color: hexToRgba(colors.textMuted, 0.08) }
                    }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        ...buildCommonTooltipColors(),
                        callbacks: {
                            label: (context) => {
                                if (context.datasetIndex === 1) {
                                    return "Industry Benchmark: 80%";
                                }
                                const row = chartData[context.dataIndex] || {};
                                return `OTD ${context.parsed.y.toFixed(1)}% | Shipments ${row.total_shipments || 0}`;
                            }
                        }
                    }
                }
            }
        });
    });
}

export function initLaneBarChart(canvasId, chartData = []) {
    return withChart(canvasId, (canvas) => {
        const colors = getChartColors();
        const labels = chartData.map((row) => {
            const lane = String(row.lane || "-");
            return lane.length > 25 ? `${lane.slice(0, 25)}...` : lane;
        });

        const values = chartData.map((row) => Number(normalizeRate(row.otd_rate || 0).toFixed(2)));
        const barColors = values.map((value) => {
            if (value >= 80) {
                return colors.success;
            }
            if (value >= 60) {
                return colors.watchAmber;
            }
            return colors.criticalRed;
        });

        return new window.Chart(canvas, {
            type: "bar",
            data: {
                labels,
                datasets: [
                    {
                        label: "OTD Rate (%)",
                        data: values,
                        backgroundColor: barColors,
                        borderRadius: 8,
                        borderSkipped: false
                    }
                ]
            },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        min: 0,
                        max: 100,
                        ticks: {
                            color: colors.textMuted,
                            callback: (value) => `${value}%`
                        },
                        grid: { color: hexToRgba(colors.textMuted, 0.08) }
                    },
                    y: {
                        ticks: { color: colors.textDark },
                        grid: { display: false }
                    }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        ...buildCommonTooltipColors(),
                        callbacks: {
                            title: (items) => {
                                const index = items[0]?.dataIndex || 0;
                                const row = chartData[index] || {};
                                return row.lane || "Lane";
                            },
                            label: (context) => {
                                const row = chartData[context.dataIndex] || {};
                                return `OTD ${context.parsed.x.toFixed(1)}% | Delay ${Number(row.avg_delay_hours || 0).toFixed(1)}h | Shipments ${row.total_shipments || 0}`;
                            }
                        }
                    }
                }
            }
        });
    });
}

function drsZonesPlugin() {
    const colors = getChartColors();
    return {
        id: "drs-zones-bg",
        beforeDraw(chart) {
            const y = chart.scales.y;
            const area = chart.chartArea;
            if (!y || !area) {
                return;
            }

            const zones = [
                { min: 0, max: 30, color: hexToRgba(colors.success, 0.08) },
                { min: 31, max: 60, color: hexToRgba(colors.watchAmber, 0.08) },
                { min: 61, max: 80, color: hexToRgba(colors.warningOrange, 0.08) },
                { min: 81, max: 100, color: hexToRgba(colors.criticalRed, 0.08) }
            ];

            const ctx = chart.ctx;
            ctx.save();
            zones.forEach((zone) => {
                const yTop = y.getPixelForValue(zone.max);
                const yBottom = y.getPixelForValue(zone.min);
                ctx.fillStyle = zone.color;
                ctx.fillRect(area.left, yTop, area.right - area.left, yBottom - yTop);
            });
            ctx.restore();
        }
    };
}

function markerLinesPlugin() {
    const colors = getChartColors();
    return {
        id: "drs-marker-lines",
        afterDatasetsDraw(chart) {
            const x = chart.scales.x;
            const area = chart.chartArea;
            if (!x || !area) {
                return;
            }

            const tickCount = chart.data.labels.length;
            if (!tickCount) {
                return;
            }

            const ctx = chart.ctx;
            const firstX = x.getPixelForValue(0);
            const lastX = x.getPixelForValue(tickCount - 1);

            ctx.save();
            ctx.strokeStyle = hexToRgba(colors.textMuted, 0.6);
            ctx.setLineDash([5, 5]);
            ctx.lineWidth = 1;

            [firstX, lastX].forEach((px) => {
                ctx.beginPath();
                ctx.moveTo(px, area.top);
                ctx.lineTo(px, area.bottom);
                ctx.stroke();
            });

            ctx.restore();
        }
    };
}

export function initDRSProjectionChart(canvasId, chartData = []) {
    return withChart(canvasId, (canvas) => {
        const colors = getChartColors();

        const labels = chartData.map((row) => {
            const timestamp = row.timestamp || row.label;
            const parsed = new Date(timestamp);
            if (Number.isNaN(parsed.getTime())) {
                return String(timestamp || "");
            }
            return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
        });

        const data = chartData.map((row) => Number(row.drs || 0));
        const finalDRS = data.length ? data[data.length - 1] : 0;

        let lineColor = colors.success;
        if (finalDRS >= 81) {
            lineColor = colors.criticalRed;
        } else if (finalDRS >= 61) {
            lineColor = colors.warningOrange;
        } else if (finalDRS >= 31) {
            lineColor = colors.watchAmber;
        }

        return new window.Chart(canvas, {
            type: "line",
            data: {
                labels,
                datasets: [
                    {
                        label: "Projected DRS",
                        data,
                        borderColor: lineColor,
                        backgroundColor: (context) => {
                            const chart = context.chart;
                            const { ctx, chartArea } = chart;
                            if (!chartArea) {
                                return hexToRgba(lineColor, 0.2);
                            }
                            const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                            gradient.addColorStop(0, hexToRgba(lineColor, 0.28));
                            gradient.addColorStop(1, hexToRgba(lineColor, 0.01));
                            return gradient;
                        },
                        fill: true,
                        tension: 0.4,
                        pointRadius: 3,
                        pointHoverRadius: 6
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        ticks: { color: colors.textMuted },
                        grid: { color: hexToRgba(colors.textMuted, 0.08) }
                    },
                    y: {
                        min: 0,
                        max: 100,
                        ticks: { color: colors.textMuted },
                        grid: { color: hexToRgba(colors.textMuted, 0.08) }
                    }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        ...buildCommonTooltipColors(),
                        callbacks: {
                            label: (context) => `Projected DRS ${context.parsed.y.toFixed(1)}`
                        }
                    }
                }
            },
            plugins: [drsZonesPlugin(), markerLinesPlugin()]
        });
    });
}

export function initDRSHistoryChart(canvasId, historyData = []) {
    return withChart(canvasId, (canvas) => {
        const colors = getChartColors();
        const labels = historyData.map((row) => {
            const parsed = new Date(row.timestamp);
            if (Number.isNaN(parsed.getTime())) {
                return String(row.timestamp || "");
            }
            return parsed.toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit"
            });
        });

        const ds = {
            tvs: { label: "TVS", color: "#0077CC" },
            mcs: { label: "MCS", color: "#3B82F6" },
            ehs: { label: "EHS", color: "#D32F2F" },
            crs: { label: "CRS", color: "#00A86B" },
            dtas: { label: "DTAS", color: "#F59E0B" },
            cps: { label: "CPS", color: "#7C3AED" }
        };

        const datasets = [
            {
                label: "DRS Total",
                data: historyData.map((row) => Number(row.drs_total || 0)),
                borderColor: colors.navy,
                backgroundColor: hexToRgba(colors.navy, 0.15),
                borderWidth: 3,
                tension: 0.35,
                fill: false,
                pointRadius: 2,
                pointHoverRadius: 5
            }
        ];

        Object.keys(ds).forEach((key) => {
            datasets.push({
                label: ds[key].label,
                data: historyData.map((row) => Number(row[key] || 0)),
                borderColor: ds[key].color,
                borderDash: [5, 4],
                borderWidth: 1.5,
                tension: 0.25,
                hidden: true,
                pointRadius: 0,
                fill: false
            });
        });

        return new window.Chart(canvas, {
            type: "line",
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                scales: {
                    x: {
                        ticks: { color: colors.textMuted },
                        grid: { color: hexToRgba(colors.textMuted, 0.08) }
                    },
                    y: {
                        min: 0,
                        max: 100,
                        ticks: { color: colors.textMuted },
                        grid: { color: hexToRgba(colors.textMuted, 0.08) }
                    }
                },
                plugins: {
                    legend: {
                        position: "bottom",
                        labels: { color: colors.textDark, usePointStyle: true }
                    },
                    tooltip: {
                        ...buildCommonTooltipColors()
                    }
                }
            },
            plugins: [drsZonesPlugin()]
        });
    });
}

export function initFleetHealthDoughnut(canvasId, healthData = {}) {
    return withChart(canvasId, (canvas) => {
        const colors = getChartColors();
        const values = [
            Number(healthData.green || 0),
            Number(healthData.watch || 0),
            Number(healthData.warning || 0),
            Number(healthData.critical || 0)
        ];
        const total = values.reduce((acc, value) => acc + value, 0);
        const healthyPct = total > 0 ? (values[0] / total) * 100 : 0;

        const centerTextPlugin = {
            id: "fleet-center-text",
            afterDraw(chart) {
                const { ctx } = chart;
                const meta = chart.getDatasetMeta(0);
                if (!meta || !meta.data || !meta.data.length) {
                    return;
                }
                const x = meta.data[0].x;
                const y = meta.data[0].y;

                ctx.save();
                ctx.fillStyle = colors.textDark;
                ctx.font = "700 28px Inter, sans-serif";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillText(`${healthyPct.toFixed(1)}%`, x, y - 6);
                ctx.fillStyle = colors.textMuted;
                ctx.font = "600 12px Inter, sans-serif";
                ctx.fillText("Fleet Health", x, y + 16);
                ctx.restore();
            }
        };

        return new window.Chart(canvas, {
            type: "doughnut",
            data: {
                labels: ["Green", "Watch", "Warning", "Critical"],
                datasets: [
                    {
                        data: values,
                        backgroundColor: [
                            colors.success,
                            colors.watchAmber,
                            colors.warningOrange,
                            colors.criticalRed
                        ],
                        borderWidth: 0
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "75%",
                plugins: {
                    legend: {
                        position: "bottom",
                        labels: {
                            color: colors.textDark,
                            generateLabels(chart) {
                                const data = chart.data.datasets[0].data;
                                const labels = chart.data.labels;
                                const sum = data.reduce((acc, value) => acc + value, 0);
                                return labels.map((label, index) => {
                                    const count = Number(data[index] || 0);
                                    const pct = sum > 0 ? (count / sum) * 100 : 0;
                                    return {
                                        text: `${label}: ${count} (${pct.toFixed(1)}%)`,
                                        fillStyle: chart.data.datasets[0].backgroundColor[index],
                                        strokeStyle: chart.data.datasets[0].backgroundColor[index],
                                        lineWidth: 1,
                                        hidden: false,
                                        index
                                    };
                                });
                            }
                        }
                    },
                    tooltip: {
                        ...buildCommonTooltipColors(),
                        callbacks: {
                            label: (context) => {
                                const value = Number(context.raw || 0);
                                const sum = context.dataset.data.reduce((acc, item) => acc + Number(item || 0), 0);
                                const pct = sum > 0 ? (value / sum) * 100 : 0;
                                return `${context.label}: ${value} (${pct.toFixed(1)}%)`;
                            }
                        }
                    }
                }
            },
            plugins: [centerTextPlugin]
        });
    });
}

export function initCarrierComparisonBarChart(canvasId, comparisonData = []) {
    return withChart(canvasId, (canvas) => {
        const colors = getChartColors();
        const labels = comparisonData.map((row) => row.carrier_name || "Carrier");

        return new window.Chart(canvas, {
            type: "bar",
            data: {
                labels,
                datasets: [
                    {
                        label: "OTD Rate",
                        data: comparisonData.map((row) => Number(normalizeRate(row.otd_rate || 0).toFixed(2))),
                        backgroundColor: colors.actionBlue,
                        borderRadius: 6
                    },
                    {
                        label: "CRS Score",
                        data: comparisonData.map((row) => Number(row.crs_score || 0)),
                        backgroundColor: colors.warningOrange,
                        borderRadius: 6
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        min: 0,
                        max: 100,
                        ticks: { color: colors.textMuted },
                        grid: { color: hexToRgba(colors.textMuted, 0.08) }
                    },
                    x: {
                        ticks: { color: colors.textDark },
                        grid: { display: false }
                    }
                },
                plugins: {
                    legend: {
                        labels: { color: colors.textDark }
                    },
                    tooltip: {
                        ...buildCommonTooltipColors(),
                        callbacks: {
                            label: (context) => `${context.dataset.label}: ${Number(context.raw || 0).toFixed(1)}`
                        }
                    }
                }
            }
        });
    });
}

function parseChartConfig(raw) {
    if (!raw) {
        return null;
    }
    try {
        return JSON.parse(raw);
    } catch (error) {
        console.warn("Invalid chart config JSON", error);
        return null;
    }
}

async function autoInitializeCharts() {
    await ensureChartJsLoaded();

    const canvases = Array.from(document.querySelectorAll("canvas[data-chart-type]"));
    for (const canvas of canvases) {
        const chartType = canvas.dataset.chartType;
        const config = parseChartConfig(canvas.dataset.chartConfig || "null");

        if (!canvas.id) {
            canvas.id = `chart-${Math.random().toString(36).slice(2, 11)}`;
        }

        if (chartType === "otd-trend") {
            await initOTDTrendChart(canvas.id, Array.isArray(config) ? config : []);
        } else if (chartType === "lane-bar") {
            await initLaneBarChart(canvas.id, Array.isArray(config) ? config : []);
        } else if (chartType === "drs-projection") {
            await initDRSProjectionChart(canvas.id, Array.isArray(config) ? config : []);
        } else if (chartType === "drs-history") {
            await initDRSHistoryChart(canvas.id, Array.isArray(config) ? config : []);
        } else if (chartType === "fleet-health-doughnut") {
            await initFleetHealthDoughnut(canvas.id, config || {});
        } else if (chartType === "carrier-comparison") {
            await initCarrierComparisonBarChart(canvas.id, Array.isArray(config) ? config : []);
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    autoInitializeCharts().catch((error) => {
        console.error("Chart initialization failed", error);
    });
});

export default {
    initOTDTrendChart,
    initLaneBarChart,
    initDRSProjectionChart,
    initDRSHistoryChart,
    initFleetHealthDoughnut,
    initCarrierComparisonBarChart,
    destroyChart,
    hexToRgba,
    getChartColors
};
