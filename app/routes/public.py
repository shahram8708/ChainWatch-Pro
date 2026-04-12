"""Public marketing and lead capture routes for ChainWatch Pro."""

from __future__ import annotations

import logging
from smtplib import SMTPException

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_mail import Message
from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.fields import EmailField
from wtforms.validators import DataRequired, Email, Length, Optional

from app.extensions import db, mail
from app.models.demo_lead import DemoLead
from app.services.notification import email_service

logger = logging.getLogger(__name__)

public_bp = Blueprint("public", __name__)


class ContactForm(FlaskForm):
    """Public contact form for inbound support and sales inquiries."""

    name = StringField("Full Name", validators=[DataRequired(), Length(min=2, max=120)])
    email = EmailField("Work Email", validators=[DataRequired(), Email()])
    company = StringField("Company", validators=[DataRequired(), Length(min=2, max=255)])
    subject = SelectField(
        "Subject",
        validators=[DataRequired()],
        choices=[
            ("General Inquiry", "General Inquiry"),
            ("Sales Question", "Sales Question"),
            ("Technical Support", "Technical Support"),
            ("Partnership", "Partnership"),
            ("Press & Media", "Press & Media"),
        ],
    )
    message = TextAreaField("Message", validators=[DataRequired(), Length(min=20, max=5000)])
    submit = SubmitField("Send Message")


class DemoRequestForm(FlaskForm):
    """Public demo request form for qualified pipeline capture."""

    first_name = StringField("First Name", validators=[DataRequired(), Length(min=2, max=100)])
    last_name = StringField("Last Name", validators=[DataRequired(), Length(min=2, max=100)])
    work_email = EmailField("Work Email", validators=[DataRequired(), Email()])
    company_name = StringField("Company Name", validators=[DataRequired(), Length(min=2, max=255)])
    job_title = StringField("Job Title", validators=[DataRequired(), Length(min=2, max=120)])
    phone = StringField("Phone (Optional)", validators=[Optional(), Length(max=40)])
    company_size = SelectField(
        "Company Size",
        validators=[DataRequired()],
        choices=[
            ("1-10", "1-10"),
            ("11-50", "11-50"),
            ("51-200", "51-200"),
            ("201-500", "201-500"),
            ("501-1000", "501-1000"),
            ("1000+", "1000+"),
        ],
    )
    monthly_shipments = SelectField(
        "Monthly Shipment Volume",
        validators=[DataRequired()],
        choices=[
            ("under_50", "Under 50"),
            ("50_200", "50-200"),
            ("200_500", "200-500"),
            ("500_2000", "500-2,000"),
            ("2000_10000", "2,000-10,000"),
            ("over_10000", "Over 10,000"),
        ],
    )
    primary_use_case = SelectField(
        "Primary Use Case",
        validators=[DataRequired()],
        choices=[
            ("Disruption Detection & Alerting", "Disruption Detection & Alerting"),
            ("Route Optimization", "Route Optimization"),
            ("Carrier Performance Analytics", "Carrier Performance Analytics"),
            ("Executive Reporting & Compliance", "Executive Reporting & Compliance"),
            ("Full Platform Evaluation", "Full Platform Evaluation"),
        ],
    )
    preferred_demo_time = SelectField(
        "Preferred Demo Time",
        validators=[DataRequired()],
        choices=[
            ("Morning (9am-12pm IST)", "Morning (9am-12pm IST)"),
            ("Afternoon (12pm-4pm IST)", "Afternoon (12pm-4pm IST)"),
            ("Evening (4pm-7pm IST)", "Evening (4pm-7pm IST)"),
        ],
    )
    message = TextAreaField("Anything else we should prepare?", validators=[Optional(), Length(max=3000)])
    submit = SubmitField("Request My Demo")


def _plan_payload() -> dict:
    return {
        "starter": {
            "name": "Starter",
            "price_monthly_inr": 12499,
            "price_annual_inr": 119990,
            "features": [
                "Up to 50 active shipments",
                "3 carrier integrations",
                "2 user seats",
                "Email alerts only",
                "Basic route optimization",
            ],
            "is_recommended": False,
            "cta_text": "Start Free Trial",
            "cta_url": "/register",
        },
        "professional": {
            "name": "Professional",
            "price_monthly_inr": 33299,
            "price_annual_inr": 319670,
            "features": [
                "Up to 500 active shipments",
                "15 carrier integrations",
                "10 user seats",
                "Email + SMS + webhook alerts",
                "Auto-generated route alternatives",
            ],
            "is_recommended": True,
            "cta_text": "Start Free Trial",
            "cta_url": "/register",
        },
        "enterprise": {
            "name": "Enterprise",
            "price_monthly_inr": None,
            "price_annual_inr": None,
            "features": [
                "Unlimited shipments, carriers, and users",
                "Dedicated CSM and onboarding",
                "SSO/SAML and advanced security",
                "Full read/write API access",
                "Custom integrations and SLA support",
            ],
            "is_recommended": False,
            "cta_text": "Contact Sales",
            "cta_url": "/demo",
        },
    }


def _blog_posts() -> list[dict]:
    posts = [
        {
            "title": "How Leading Ops Teams Build a 6-Hour Disruption Response Advantage",
            "slug": "build-six-hour-disruption-response-advantage",
            "excerpt": "Teams that detect disruption early are not lucky. They are operating a deliberate response system that combines carrier telemetry, lane-level risk signals, and decision governance.",
            "category": "Supply Chain Strategy",
            "author_name": "Ananya Rao",
            "author_role": "VP, Supply Chain Strategy",
            "author_company": "ChainWatch Pro",
            "published_date": "April 02, 2026",
            "read_time_minutes": 9,
            "featured": True,
            "content_html": """
<h2>Disruption speed is now a competitive moat</h2>
<p>Most logistics leaders still evaluate disruption performance using lagging metrics: late delivery rates, exception tickets, or customer escalations. Those indicators matter, but they describe outcomes that already happened. The best operations teams have shifted to lead indicators that measure how quickly they identify risk, how rapidly they choose alternatives, and how confidently they execute interventions before service level agreements are compromised. In practical terms, they have converted disruption management from a firefighting routine into a repeatable operating system.</p>
<p>The gap between reactive and proactive teams is often just a few hours. In electronics, automotive, and e-commerce networks, a six-hour head start can be the difference between rolling inventory through a secondary gateway versus paying premium expedite rates. It can mean preserving promised delivery windows during port congestion instead of issuing customer credits. And it can allow planners to protect high-margin orders first, rather than treating all delayed shipments as equal.</p>
<h2>What early-warning teams do differently</h2>
<p>High-performing teams monitor risk as a continuously updated score, not a daily status report. They blend internal data such as booking details, carrier milestones, and promised delivery dates with external context including weather severity, congestion indexes, labor actions, and route instability. Instead of asking, "Is this shipment delayed yet?" they ask, "How likely is this shipment to miss commitment in the next 6-48 hours?" That question changes both tooling and behavior.</p>
<p>They also establish threshold-based playbooks. When a shipment crosses warning risk, it is routed to an operations queue with suggested checks. When it crosses critical risk, escalation rules trigger instantly, and pre-approved alternatives are presented with cost and time trade-offs. This removes decision friction. Teams no longer debate whether to act; they evaluate how to act.</p>
<h2>Three process changes that create measurable speed</h2>
<p><strong>1) Segment by business impact, not only by route.</strong> A shipment carrying low-margin replenishment stock should not consume the same urgency as one supporting launch inventory. Advanced teams assign impact weights based on margin contribution, customer tier, and stock-out risk. This allows intervention capacity to be focused where it protects revenue and trust.</p>
<p><strong>2) Build an exception ownership matrix.</strong> Many disruptions stall because ownership is unclear across logistics, procurement, customer operations, and regional teams. Define who acknowledges each risk class, who approves reroutes, and who communicates externally. When roles are pre-assigned, execution speed improves without sacrificing governance.</p>
<p><strong>3) Standardize decision artifacts.</strong> Every intervention should be accompanied by a compact decision record: risk score, triggering signals, selected option, expected ETA delta, and cost impact. Teams that keep this audit trail improve faster because post-incident reviews become evidence-based rather than anecdotal.</p>
<blockquote>Speed without structure creates chaos. Structure without speed creates losses. High-maturity operations teams design for both.</blockquote>
<h2>Where technology should assist, not replace, operators</h2>
<p>There is a persistent misconception that proactive logistics requires fully autonomous rerouting. In reality, most organizations gain the largest value from decision support long before full automation. AI should prioritize, score, and rank options. Humans should retain control over financial and customer-impacting decisions, especially during the transition phase. This hybrid model accelerates response while preserving accountability.</p>
<p>Another important principle is transparency. Recommendation confidence, assumptions, and data freshness should be visible. Operators are more likely to trust and use AI guidance when they understand why a route is being ranked first and what trade-offs are involved. Explainability is not a compliance checkbox; it is a workflow adoption driver.</p>
<h2>How to measure whether your response advantage is real</h2>
<p>Beyond on-time delivery, track operational leading indicators each week:</p>
<ul>
<li>Median time from risk threshold breach to first acknowledgment</li>
<li>Median time from acknowledgment to decision</li>
<li>Percent of critical disruptions handled before SLA breach</li>
<li>Cost delta between planned and executed intervention portfolios</li>
<li>Recurrence rate of the same disruption pattern by lane and carrier</li>
</ul>
<p>Teams that improve these metrics usually see downstream gains in customer communication quality, reduced premium freight spend, and fewer avoidable escalations. They also tend to retain planner talent better because daily work becomes strategic, not purely reactive.</p>
<h2>Turning strategy into a 30-day execution plan</h2>
<p>Week one should focus on visibility hygiene: unify shipment references, standardize status definitions, and map ownership for high-risk lanes. Week two should introduce risk thresholds and operational queues with clear response SLAs. Week three should activate route alternative evaluation templates so teams can decide quickly with comparable cost-time inputs. Week four should institutionalize weekly review cadences that examine both successful interventions and misses.</p>
<p>Organizations that execute this sequence often discover an immediate benefit: morning status meetings shrink because teams no longer spend most of their time collecting data. Instead, they discuss decisions and outcomes. That is the hallmark of a mature disruption response engine.</p>
<p>Proactive logistics is not a single feature. It is a capability stack made of signal quality, operating discipline, and cross-functional trust. Build those foundations, and the six-hour response advantage becomes repeatable across lanes, carriers, and regions.</p>
""",
        },
        {
            "title": "From Static ETAs to Predictive ETAs: What Actually Improves Reliability",
            "slug": "static-eta-to-predictive-eta-reliability",
            "excerpt": "Predictive ETAs are not just better math. They are better operational decisions fed by richer context and continuously recalibrated assumptions.",
            "category": "Technology",
            "author_name": "Rahul Menon",
            "author_role": "Principal AI Engineer",
            "author_company": "ChainWatch Pro",
            "published_date": "March 27, 2026",
            "read_time_minutes": 11,
            "featured": False,
            "content_html": """
<h2>Why static ETAs fail in modern networks</h2>
<p>Traditional ETA logic assumes stable transit conditions. It often starts with historical average transit time, then adds simple buffers. That approach worked when route variability was modest and customer tolerance was high. Today, variability is structural. Port congestion waves, weather volatility, labor constraints, and dynamic carrier prioritization mean that the same lane can have materially different outcomes week to week.</p>
<p>Static ETA systems fail because they underreact to new signals. They treat events as exceptions instead of core inputs. By the time delay is obvious in the milestone feed, customer communication windows may already be missed and rerouting options may be expensive or unavailable.</p>
<h2>Predictive ETA is an operating model, not just a model artifact</h2>
<p>High-quality predictive ETA systems continuously ingest and re-weight diverse signals: vessel schedules, dwell times at transshipment points, customs clearance latency, weather severity, airport and terminal load, and carrier reliability trends. But data alone is insufficient. The system must also fit into real workflows where planners act on confidence intervals, not single-point estimates.</p>
<p>Mature teams avoid the trap of asking for one definitive ETA number. Instead, they ask for probability bands and scenario paths. For instance, a shipment might have a 60% chance of arriving by Tuesday evening, 30% by Wednesday morning, and 10% by Wednesday evening based on current conditions. This allows customer success, inventory, and transport teams to plan contingencies proportionately.</p>
<h2>Four technical foundations that matter most</h2>
<p><strong>Event normalization.</strong> Carrier and partner feeds rarely share identical semantics. One network may mark a container as "arrived" when it reaches anchorage, another when it clears terminal gates. Predictive systems need robust event mapping so the model reads consistent state transitions.</p>
<p><strong>Context enrichment.</strong> External intelligence should not be bolted on as an afterthought. Weather alerts, geopolitical advisories, and seasonal capacity constraints need to be time-aligned to each shipment leg. Good systems enrich context per leg and recalculate expected impact by mode and lane.</p>
<p><strong>Lane-specific calibration.</strong> Global averages mask route behavior. A lane with chronic customs volatility requires different weighting than a lane where weather dominates delays. Calibration by corridor, mode, and carrier class is essential for reliable output.</p>
<p><strong>Feedback loops.</strong> Model performance degrades without closed-loop learning. Compare predicted arrival distributions to actual arrivals, identify systematic bias, and retrain frequently. This is where MLOps discipline directly impacts service outcomes.</p>
<h2>Operationalizing predictive ETAs in day-to-day execution</h2>
<p>Predictive ETAs become valuable only when tied to intervention thresholds. Teams should define threshold triggers by business context. For high-value cargo, a 25% probability of delay may justify early intervention. For lower-priority flows, threshold may be 50% or higher. The right threshold is not universal; it depends on customer commitments and margin sensitivity.</p>
<p>Notification design matters as well. Too many low-value alerts create fatigue and reduce trust in the system. Too few alerts eliminate proactive opportunities. Effective programs tune alert frequency and severity based on user role. Operations managers may need immediate exceptions, while executive stakeholders prefer concise periodic summaries with trend indicators.</p>
<blockquote>Predictive ETA is successful when it changes decisions early enough to protect outcomes, not when it merely reports delays more accurately.</blockquote>
<h2>Common implementation mistakes</h2>
<p>One frequent mistake is deploying predictive ETA without data quality governance. Missing timestamps, inconsistent time zones, and duplicate milestones can produce apparent model drift that is actually a data pipeline issue. Another mistake is overfitting early pilots to a narrow set of lanes, then expecting equivalent performance in new regions without recalibration.</p>
<p>A third issue is failing to include business users in metric definition. Data science teams often optimize for statistical accuracy, while operations teams care about actionability. Both are important. Track MAE or quantile loss, but also track disruption prevention rate and intervention lead time.</p>
<h2>What reliability gains look like in practice</h2>
<p>Organizations that properly deploy predictive ETA systems report measurable improvements: tighter promise windows, fewer unexpected delays in customer communication, and better inventory planning at destination nodes. They also unlock better carrier negotiations because performance discussions move from anecdotal incidents to consistent lane-level evidence.</p>
<p>Critically, predictive systems enable earlier cost control. If delay probability rises early, teams can evaluate lower-cost alternatives before premium urgency pricing applies. Over time, this shifts spend from emergency remediation toward planned resilience investments.</p>
<h2>A pragmatic rollout sequence</h2>
<p>Start with your top lanes by volume and customer criticality. Build robust event mapping first. Introduce confidence-band ETAs and monitor user adoption patterns. Add threshold-based alerts with clear ownership. Then expand corridor by corridor while continuously validating performance. This staged approach delivers value quickly while preventing broad, fragile rollouts.</p>
<p>Predictive ETA is no longer optional for teams promising dependable delivery in uncertain conditions. The organizations that master it will not eliminate disruption, but they will consistently outperform competitors in how quickly and effectively they respond to it.</p>
""",
        },
        {
            "title": "Carrier Scorecards That Drive Better Contracts, Not Just Better Slides",
            "slug": "carrier-scorecards-drive-better-contracts",
            "excerpt": "Carrier intelligence is only valuable when it influences allocation, contractual incentives, and lane strategy. Otherwise it remains reporting theatre.",
            "category": "Carrier Intelligence",
            "author_name": "Nikhil Bhat",
            "author_role": "Director, Carrier Analytics",
            "author_company": "ChainWatch Pro",
            "published_date": "March 18, 2026",
            "read_time_minutes": 10,
            "featured": False,
            "content_html": """
<h2>The gap between carrier reporting and carrier strategy</h2>
<p>Most teams already have some form of carrier report. Many produce monthly decks with on-time percentages, damage rates, and claim volumes. Yet the same carriers are re-awarded the same lanes, under similar terms, cycle after cycle. When scorecards do not shape allocation or incentives, they become retrospective storytelling rather than strategic tools.</p>
<p>A useful scorecard must answer commercial questions: which carriers are overperforming relative to cost? which are introducing hidden variability? where should volume be shifted? and which terms should be renegotiated based on evidence rather than perception? If your scorecard cannot inform those decisions, it needs redesign.</p>
<h2>Designing scorecards for action</h2>
<p>Begin with lane-level segmentation. Aggregate global metrics often hide meaningful divergence. A carrier may excel on intra-Asia routes but underperform on East Asia to Europe due to transshipment complexity. Scorecards should therefore evaluate performance by corridor, mode, and service type.</p>
<p>Next, combine service and economics. On-time delivery alone is incomplete if the carrier consistently requires premium surcharges during peak periods. Likewise, lowest base rate is misleading if delay volatility triggers expensive downstream interventions. Balanced scorecards include reliability, predictability, claim ratio, escalation frequency, and total landed cost impact.</p>
<h2>Five metrics that change negotiation outcomes</h2>
<ul>
<li><strong>Commitment adherence:</strong> Percent of shipments meeting promised windows at booking, not just revised ETAs.</li>
<li><strong>Schedule volatility index:</strong> How often milestone timelines change after dispatch.</li>
<li><strong>Exception resolution velocity:</strong> Time from issue detection to meaningful carrier response.</li>
<li><strong>Lane-adjusted reliability:</strong> Performance normalized by lane risk profile to avoid unfair comparisons.</li>
<li><strong>Intervention burden:</strong> Internal operational effort required per 100 shipments for that carrier.</li>
</ul>
<p>These measures reveal both visible and hidden operational costs. They also create a common language for procurement and operations teams when discussing annual or quarterly terms.</p>
<h2>Building incentive structures around data</h2>
<p>When scorecards are mature, contract terms can become more dynamic. Instead of flat service credits, organizations can adopt performance corridors with upside and downside adjustments tied to measurable outcomes. For example, carriers exceeding reliability thresholds on priority lanes may receive higher allocation shares. Carriers consistently missing commitments may face reduced share or corrective action plans with time-bound milestones.</p>
<p>Data-backed incentives create a healthier relationship. Carriers see transparent expectations and can invest where it matters most. Shippers gain leverage grounded in objective evidence, reducing negotiation friction driven by isolated incidents.</p>
<blockquote>What gets measured can be discussed. What gets linked to allocation gets changed.</blockquote>
<h2>Governance practices that keep scorecards credible</h2>
<p>Scorecard trust depends on methodology clarity. Define every metric, data source, and exception rule. If a storm closes a major port, decide upfront how force majeure events are represented. Without clear methodology, results will be disputed and adoption will decline.</p>
<p>Cross-functional governance is equally important. Procurement, logistics operations, finance, and customer-facing teams should all participate in metric reviews. This prevents unilateral optimization where one function improves its KPI while another absorbs the cost.</p>
<h2>Avoiding common pitfalls</h2>
<p>Do not refresh scorecards too infrequently. Quarterly updates are often too slow for fast-moving lanes. Monthly or even weekly views are ideal for volatile corridors. Also avoid overweighting historical averages. Recent trend direction is often more predictive of near-term risk than long-run means.</p>
<p>Another frequent pitfall is failing to separate controllable versus uncontrollable variance. If a carrier is penalized for macro disruptions outside its control without context, the scorecard can become adversarial and less useful. Balanced frameworks recognize external constraints while still evaluating response quality.</p>
<h2>From report to reallocation</h2>
<p>The strongest signal that your scorecard is working is allocation change. If top performers consistently gain strategic lanes and weaker performers are moved to corrective pathways, the program is influencing real decisions. Over time, this creates a portfolio effect: better carrier mix, lower exception volume, and more predictable delivery performance.</p>
<p>Ultimately, carrier intelligence should strengthen both resilience and commercial discipline. Organizations that use scorecards as decision engines, not presentation artifacts, will negotiate better terms and deliver more consistent customer outcomes in an increasingly volatile logistics environment.</p>
""",
        },
        {
            "title": "Building a Risk Heatmap That Ops Teams Actually Use",
            "slug": "risk-heatmap-ops-teams-actually-use",
            "excerpt": "The best risk maps reduce cognitive load. They surface what changed, what matters now, and what action should be taken first.",
            "category": "Risk Management",
            "author_name": "Meera Kulkarni",
            "author_role": "Head of Product",
            "author_company": "ChainWatch Pro",
            "published_date": "March 10, 2026",
            "read_time_minutes": 8,
            "featured": False,
            "content_html": """
<h2>Why many risk dashboards fail adoption</h2>
<p>Risk maps are common in supply chain platforms, yet many remain underused after initial rollout. The typical failure mode is visual overload. Teams are shown hundreds of markers, color gradients, and data panels without clear prioritization. In a fast-paced operations environment, users do not need more maps; they need faster decisions.</p>
<p>A useful heatmap should answer three questions within seconds: what changed since my last check, where is service risk escalating, and which shipments require action now. If these answers are buried behind filters and manual investigation, adoption drops and teams revert to spreadsheets and email threads.</p>
<h2>Design principle one: prioritize delta over density</h2>
<p>Most map views display current state well enough. The real value lies in change detection. Highlight newly escalated corridors, rising congestion nodes, and risk clusters that crossed threshold recently. Use subtle context layers for stable zones and high-contrast signals for material deltas.</p>
<p>This approach reduces cognitive load. Operators should not scan static noise to discover movement. They should immediately see where risk is accelerating.</p>
<h2>Design principle two: combine geographic and operational context</h2>
<p>Geography alone is insufficient. A red zone on the map becomes actionable only when linked to shipments, commitments, and customer impact. Effective heatmaps connect geospatial signals with operational metadata: impacted shipment count, highest risk score, average ETA slippage, and financial exposure estimates.</p>
<p>When users can drill from region to lane to shipment in one flow, handoffs become faster. The map is no longer a visual ornament. It becomes an operational control surface.</p>
<h2>Design principle three: make thresholds explicit</h2>
<p>Color without semantics invites interpretation drift. Define clear thresholds and ensure everyone understands them. For example, warning may represent disruption risk score between 60 and 79, while critical begins at 80. Pair color with labels and confidence cues so decisions are based on shared criteria.</p>
<p>Threshold transparency also improves accountability. Teams can evaluate whether interventions were triggered appropriately and refine policy over time.</p>
<blockquote>A risk map should compress complexity, not amplify it.</blockquote>
<h2>What high-adoption heatmaps include</h2>
<ul>
<li>Role-based presets for planners, managers, and executives</li>
<li>Time-window controls to view current, 24-hour, and 72-hour risk horizons</li>
<li>Lane overlays showing risk trajectory, not just current status</li>
<li>One-click jump from hotspot to ranked intervention options</li>
<li>Audit breadcrumbs recording who acknowledged and acted</li>
</ul>
<p>These elements keep users in one workflow from detection through action. Every additional context switch increases response latency.</p>
<h2>Operational habits that reinforce usage</h2>
<p>Technology alone cannot guarantee adoption. Teams should establish map-first operating rituals, especially during morning and shift-change huddles. Start with top risk deltas, assign owners, and define decision deadlines. This practice turns the heatmap into the default coordination artifact rather than a secondary reference.</p>
<p>Leadership behavior matters too. When managers ask for evidence from the heatmap during reviews, usage quality improves. Over time, teams become more precise in threshold tuning and intervention timing because decisions are consistently documented.</p>
<h2>Measuring success beyond clicks</h2>
<p>Do not rely only on page views. Evaluate behavioral impact: reduced time to acknowledgment, increased pre-SLA interventions, lower premium freight spend, and improved customer communication lead time. If these indicators improve, the heatmap is functioning as intended.</p>
<p>Also monitor false positive burden. If users repeatedly investigate high-risk signals that do not materialize, trust erodes. Continuous calibration of scoring logic and context signals is essential to maintain credibility.</p>
<h2>From map to momentum</h2>
<p>An effective risk heatmap creates shared situational awareness across operations, procurement, and customer teams. It aligns everyone on where to act and why, reducing debate and response delay. In volatile logistics environments, this alignment can be the difference between managed disruption and cascading service failures.</p>
<p>The goal is not to predict every disruption perfectly. The goal is to ensure the organization sees meaningful risk early, agrees on priority quickly, and executes with confidence. Design your heatmap for that outcome, and teams will use it every day.</p>
""",
        },
        {
            "title": "What 2026 Port Congestion Patterns Mean for Asia-Europe Lanes",
            "slug": "2026-port-congestion-patterns-asia-europe",
            "excerpt": "Congestion is no longer a seasonal spike. It behaves like a rotating constraint across hubs, making dynamic routing and early warning more critical than ever.",
            "category": "Industry Trends",
            "author_name": "Ishaan Kapoor",
            "author_role": "Market Intelligence Lead",
            "author_company": "ChainWatch Pro",
            "published_date": "March 01, 2026",
            "read_time_minutes": 12,
            "featured": False,
            "content_html": """
<h2>Congestion has become rotational, not exceptional</h2>
<p>For years, teams treated port congestion as a periodic shock. In 2026, the pattern is different. Congestion now rotates across major hubs, driven by demand swings, vessel schedule compression, inland bottlenecks, and labor variability. As one node recovers, pressure shifts downstream. This rotational behavior creates persistent uncertainty for Asia-Europe corridors.</p>
<p>The practical implication is clear: static lane plans age quickly. A route that looked stable at booking may become high-risk within days. Organizations that review routing only at dispatch are absorbing avoidable volatility.</p>
<h2>Key signals operators should track weekly</h2>
<p><strong>Berth waiting time trend.</strong> Absolute waiting time matters, but acceleration matters more. A steady increase over two weeks often predicts downstream schedule instability.
<strong>Transshipment dwell variability.</strong> Even moderate dwell increases at transshipment hubs can cascade into missed feeder windows.
<strong>Blank sailing frequency.</strong> Rising blank sailings reduce schedule resilience and amplify reliance on fewer departure options.
<strong>Inland terminal throughput.</strong> Port throughput can appear healthy while inland evacuation is constrained, creating hidden backlog risk.</p>
<p>Teams that combine these indicators with shipment-level commitments can intervene earlier and with lower cost.</p>
<h2>How shippers are adapting route strategy</h2>
<p>Leading organizations are diversifying gateway usage and pre-approving fallback corridors for critical SKUs. Rather than committing entire volume to one preferred lane, they maintain option portfolios with defined trigger conditions. When risk thresholds are crossed, planners can execute approved alternatives quickly instead of reopening full procurement cycles.</p>
<p>Another trend is tighter collaboration with carriers on service reliability transparency. Shippers increasingly request lane-specific reliability disclosures and exception response expectations, not only rate sheets. This pushes conversations from price-only negotiations toward reliability-adjusted value.</p>
<h2>Cost impact: visible and hidden</h2>
<p>Congestion costs are often underestimated because only direct freight premiums are measured. Hidden costs include inventory imbalance, production resequencing, customer service load, and expedited last-mile compensation. When these effects are included, proactive rerouting can be financially justified even when direct freight cost increases modestly.</p>
<p>Finance and operations teams should model interventions using full landed impact, not transport line items alone. This approach leads to more rational decision-making under uncertainty.</p>
<blockquote>In rotational congestion environments, optionality is not a luxury. It is a core resilience asset.</blockquote>
<h2>Technology capabilities now required for lane resilience</h2>
<ul>
<li>Continuous disruption scoring at shipment and lane level</li>
<li>Scenario comparison across cost, transit time, and confidence</li>
<li>Carrier performance benchmarking by corridor and period</li>
<li>Alerting workflows tied to operational ownership</li>
<li>Decision logs for post-event learning and governance</li>
</ul>
<p>Without these capabilities, teams are effectively making high-stakes decisions with stale context.</p>
<h2>What to expect through the next planning cycle</h2>
<p>Current indicators suggest continued volatility in handoff-sensitive routes and periodic pressure around major transshipment hubs. Weather and labor events will likely remain multipliers rather than standalone causes. This means teams should prepare for overlapping risk signals rather than single-cause disruptions.</p>
<p>Plan cycles should include monthly lane stress tests. Evaluate how routes perform under elevated dwell, reduced vessel frequency, and customs delay scenarios. Organizations that pre-model these conditions can shift from reactive improvisation to structured response.</p>
<h2>A five-step action framework for Asia-Europe operators</h2>
<ol>
<li>Define critical shipment cohorts by customer and margin impact.</li>
<li>Establish lane-level warning and critical thresholds.</li>
<li>Pre-negotiate fallback carrier and gateway options.</li>
<li>Run weekly reliability reviews with procurement and operations together.</li>
<li>Document intervention outcomes to refine playbooks quarterly.</li>
</ol>
<p>This framework is pragmatic and scalable. It does not require full automation on day one, but it does require disciplined visibility and decision governance.</p>
<h2>Strategic takeaway</h2>
<p>Asia-Europe lanes will continue to be commercially vital and operationally complex. The winners will not be those who avoid disruption entirely. They will be those who detect risk earlier, decide faster, and execute alternatives with better confidence. Congestion is no longer a surprise event. It is an operating condition. Treat it accordingly.</p>
""",
        },
        {
            "title": "Scenario Planning for Peak Season: A Practical Framework for Logistics Leaders",
            "slug": "peak-season-scenario-planning-framework",
            "excerpt": "Peak season success comes from pre-committed decisions. Scenario planning turns uncertainty into a prepared set of actions.",
            "category": "Supply Chain Strategy",
            "author_name": "Karthik Iyer",
            "author_role": "Senior Solutions Consultant",
            "author_company": "ChainWatch Pro",
            "published_date": "February 21, 2026",
            "read_time_minutes": 10,
            "featured": False,
            "content_html": """
<h2>Peak season failures are usually planning failures</h2>
<p>When peak season disruptions occur, post-mortems often cite external volatility. While external events are real, many service failures trace back to insufficient pre-commitment. Teams knew where pressure might emerge but had not defined triggers, owners, or fallback actions. As a result, decisions were made late, under stress, and at high cost.</p>
<p>Scenario planning addresses this gap by converting uncertainty into a set of prepared choices. It does not eliminate surprise. It reduces response time and improves decision quality when surprise arrives.</p>
<h2>Start with the right scenario set</h2>
<p>Avoid creating dozens of low-probability scenarios that overwhelm teams. Focus on a compact portfolio that reflects your actual exposure. For most global operators, five scenario families are sufficient: port congestion surge, carrier capacity squeeze, customs processing delays, weather-driven route instability, and demand spike mismatch.</p>
<p>Each scenario should be quantified across three dimensions: service impact probability, financial impact range, and controllability. This helps leadership prioritize preparation effort where it creates the highest resilience return.</p>
<h2>Define trigger thresholds before the season starts</h2>
<p>A scenario without trigger thresholds is only a discussion artifact. Translate each scenario into measurable activation conditions. For example, if average dwell exceeds a defined threshold for two consecutive weeks in a critical hub, activate gateway diversification playbook. If carrier cancellation rate rises beyond threshold, activate allocation rebalance and customer communication protocol.</p>
<p>Threshold clarity avoids delayed debates and ensures cross-functional teams respond consistently across regions.</p>
<h2>Pre-build decision options with trade-offs</h2>
<p>For every high-priority scenario, create two to three intervention options with quantified trade-offs. Include expected ETA impact, incremental cost, confidence level, and operational feasibility. This allows teams to move from analysis to execution in minutes rather than hours.</p>
<p>Importantly, pre-align finance and commercial stakeholders on acceptable cost corridors. Many interventions fail because financial approval loops are triggered too late. Pre-approved ranges protect response speed while maintaining governance.</p>
<blockquote>Scenario planning is valuable only when it shortens real decisions during live disruption.</blockquote>
<h2>Assign ownership and communication pathways</h2>
<p>Prepared options are not enough if ownership is ambiguous. Define who detects threshold breaches, who validates signal quality, who approves interventions, and who communicates with customers. Keep this matrix visible and role-based. During peak windows, clarity of ownership reduces escalation noise and duplicated effort.</p>
<p>Customer communication templates should also be pre-drafted. Timely, transparent communication often protects trust even when delays occur. Teams that wait for perfect certainty before communicating usually lose both time and credibility.</p>
<h2>Run simulation drills, not just planning meetings</h2>
<p>Tabletop exercises expose execution gaps before they become expensive. Simulate threshold breaches and test whether teams can move from alert to decision within target SLAs. Track where handoffs stall, where data is missing, and where approvals bottleneck. Then update playbooks accordingly.</p>
<p>Even one or two drills before peak season can materially improve operational readiness and stakeholder alignment.</p>
<h2>Metrics for a strong scenario program</h2>
<ul>
<li>Time from threshold breach to decision</li>
<li>Percent of disruptions handled via pre-defined playbooks</li>
<li>Premium freight spend variance vs. baseline forecast</li>
<li>Customer communication lead time before commitment risk</li>
<li>Post-season recurrence of previously known failure patterns</li>
</ul>
<p>These measures reveal whether scenario planning is operationalized or merely documented.</p>
<h2>A realistic 45-day implementation path</h2>
<p>Weeks 1-2: define scenario set and threshold metrics. Weeks 3-4: build intervention options and align financial guardrails. Weeks 5-6: assign owners, draft communication templates, and publish governance matrix. Week 7: run simulation drills and adjust. This sequence is practical for most mid-size and enterprise operations teams preparing for peak periods.</p>
<p>Teams that execute this plan typically enter peak season with greater confidence and lower decision latency. They still face disruption, but they face it with a prepared system instead of improvised response.</p>
<h2>Final perspective</h2>
<p>Peak season does not reward optimism; it rewards preparedness. Scenario planning gives leaders a disciplined way to convert uncertainty into actionable readiness. In a market where customer expectations remain high and disruption remains persistent, that readiness is one of the strongest competitive advantages you can build.</p>
""",
        },
    ]
    return posts


@public_bp.route("/")
def home():
    """Render public landing page for all users, including authenticated users."""

    return render_template(
        "public/home.html",
        page_title="ChainWatch Pro — AI-Powered Supply Chain Intelligence",
        meta_description="Real-time supply chain disruption detection and dynamic route optimization. See the disruption before it sees you.",
        plans=_plan_payload(),
    )


@public_bp.route("/features")
def features():
    """Render public features overview page."""

    return render_template(
        "public/features.html",
        page_title="Platform Features — ChainWatch Pro",
        meta_description="Explore ChainWatch Pro features: unified shipment tracking, AI disruption detection, dynamic route optimization, carrier intelligence, and more.",
    )


@public_bp.route("/pricing")
def pricing():
    """Render pricing page with plan metadata and CTA links."""

    return render_template(
        "public/pricing.html",
        page_title="Pricing Plans — ChainWatch Pro",
        meta_description="Transparent pricing for every team size. Start free for 14 days. Starter ₹12,499/mo, Professional ₹33,299/mo, Enterprise custom.",
        plans=_plan_payload(),
    )


@public_bp.route("/about")
def about():
    """Render company story and mission page."""

    return render_template("public/about.html", page_title="About ChainWatch Pro")


@public_bp.route("/contact", methods=["GET", "POST"])
def contact():
    """Handle contact form submissions and acknowledgement emails."""

    form = ContactForm()

    if form.validate_on_submit():
        sender = current_app.config["MAIL_DEFAULT_SENDER"]
        owner_recipients = [current_app.config["MAIL_DEFAULT_SENDER"]]

        owner_msg = Message(
            subject=f"New Contact Form Submission: {form.subject.data}",
            recipients=owner_recipients,
            sender=sender,
        )
        owner_msg.body = (
            "A new contact request was submitted on chainwatchpro.com\n\n"
            f"Name: {form.name.data}\n"
            f"Email: {form.email.data}\n"
            f"Company: {form.company.data}\n"
            f"Subject: {form.subject.data}\n\n"
            "Message:\n"
            f"{form.message.data}\n"
        )

        auto_msg = Message(
            subject="We received your message - ChainWatch Pro",
            recipients=[form.email.data],
            sender=sender,
        )
        auto_msg.body = (
            f"Hi {form.name.data},\n\n"
            "Thank you for contacting ChainWatch Pro. "
            "Our team will get back to you within 24 hours.\n\n"
            "If your request is urgent, reply to this email and include your preferred callback time.\n\n"
            "Regards,\n"
            "ChainWatch Pro Team"
        )

        try:
            mail.send(owner_msg)
            mail.send(auto_msg)
            flash("Thank you! We'll get back to you within 24 hours.", "success")
            return redirect(
                url_for(
                    "public.contact",
                    submitted="true",
                    name=form.name.data,
                    email=form.email.data,
                )
            )
        except SMTPException:
            logger.exception("SMTPException while processing contact form")
            flash(
                "Message received but email confirmation may be delayed. We'll still get back to you.",
                "warning",
            )
            return redirect(url_for("public.contact"))

    return render_template(
        "public/contact.html",
        form=form,
        submitted=request.args.get("submitted") == "true",
        submitted_name=request.args.get("name", ""),
        submitted_email=request.args.get("email", ""),
        page_title="Contact ChainWatch Pro",
    )


@public_bp.route("/demo", methods=["GET", "POST"])
def demo():
    """Capture demo requests and send both customer and internal notifications."""

    form = DemoRequestForm()

    if form.validate_on_submit():
        lead = DemoLead(
            first_name=form.first_name.data.strip(),
            last_name=form.last_name.data.strip(),
            email=form.work_email.data.strip().lower(),
            company_name=form.company_name.data.strip(),
            job_title=form.job_title.data.strip(),
            phone=(form.phone.data or "").strip() or None,
            company_size=form.company_size.data,
            monthly_shipments=form.monthly_shipments.data,
            primary_use_case=form.primary_use_case.data,
            preferred_demo_time=form.preferred_demo_time.data,
            message=(form.message.data or "").strip() or None,
        )

        try:
            db.session.add(lead)
            db.session.commit()

            sender = current_app.config["MAIL_DEFAULT_SENDER"]
            sales_recipient = current_app.config.get("SALES_TEAM_EMAIL", "sales@chainwatchpro.com")

            confirmation_msg = Message(
                subject="Your ChainWatch Pro demo request is confirmed",
                recipients=[lead.email],
                sender=sender,
            )
            confirmation_msg.body = (
                f"Hi {lead.first_name},\n\n"
                "Thank you for requesting a personalized ChainWatch Pro demo. "
                "A supply chain specialist will reach out within 4 business hours.\n\n"
                "We will tailor the session around your use case: "
                f"{lead.primary_use_case}.\n\n"
                "Regards,\n"
                "ChainWatch Pro Team"
            )

            internal_msg = Message(
                subject=f"New Demo Request: {lead.company_name}",
                recipients=[sales_recipient],
                sender=sender,
            )
            internal_msg.body = (
                "New demo request received\n\n"
                f"Name: {lead.first_name} {lead.last_name}\n"
                f"Email: {lead.email}\n"
                f"Company: {lead.company_name}\n"
                f"Title: {lead.job_title}\n"
                f"Company size: {lead.company_size}\n"
                f"Monthly shipments: {lead.monthly_shipments}\n"
                f"Use case: {lead.primary_use_case}\n"
                f"Preferred time: {lead.preferred_demo_time}\n"
                f"Phone: {lead.phone or '-'}\n\n"
                f"Notes:\n{lead.message or '-'}"
            )

            mail.send(confirmation_msg)
            mail.send(internal_msg)

            flash("Thanks! Your demo request is in. Our team will contact you within 4 business hours.", "success")
            return redirect(url_for("public.demo", submitted="true"))
        except SMTPException:
            logger.exception("SMTPException while sending demo emails for lead=%s", lead.email)
            flash(
                "Demo request saved, but email confirmation may be delayed. Our team will still contact you.",
                "warning",
            )
            return redirect(url_for("public.demo", submitted="true"))
        except Exception:
            db.session.rollback()
            logger.exception("Unexpected error while saving demo lead")
            flash("We could not process your request right now. Please try again.", "danger")

    return render_template(
        "public/demo.html",
        form=form,
        submitted=request.args.get("submitted") == "true",
        page_title="Request a Personalized Demo - ChainWatch Pro",
        meta_description="Book a personalized ChainWatch Pro demo and see AI-powered disruption monitoring, route optimization, and carrier intelligence in action.",
    )


@public_bp.route("/blog")
def blog():
    """Render editorial blog listing page."""

    posts = _blog_posts()
    featured_post = next((item for item in posts if item.get("featured")), posts[0])
    remaining_posts = [item for item in posts if item["slug"] != featured_post["slug"]]

    return render_template(
        "public/blog.html",
        page_title="Supply Chain Intelligence Blog - ChainWatch Pro",
        posts=posts,
        featured_post=featured_post,
        remaining_posts=remaining_posts,
    )


@public_bp.route("/blog/<slug>")
def blog_post(slug: str):
    """Render a single long-form blog article by slug."""

    posts = _blog_posts()
    post = next((item for item in posts if item["slug"] == slug), None)
    if post is None:
        abort(404)

    related_posts = [
        item for item in posts if item["slug"] != slug and item["category"] == post["category"]
    ]
    if len(related_posts) < 2:
        fallback = [item for item in posts if item["slug"] != slug]
        related_posts.extend(fallback[: max(0, 2 - len(related_posts))])

    return render_template(
        "public/blog_post.html",
        page_title=f"{post['title']} - ChainWatch Pro Blog",
        post=post,
        related_posts=related_posts[:2],
    )


@public_bp.route("/privacy")
def privacy():
    """Render privacy policy page for public website."""

    return render_template("public/privacy.html", page_title="Privacy Policy - ChainWatch Pro")


@public_bp.route("/terms")
def terms():
    """Render terms of service page for public website."""

    return render_template("public/terms.html", page_title="Terms of Service - ChainWatch Pro")
