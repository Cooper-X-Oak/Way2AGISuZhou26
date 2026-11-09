"""Declarative Bible v1.0 workshop catalogue and scenario routing.

Scenario-specific input/rework/tool declarations are implementation configuration,
not claims of available tools or measured business coverage. Tools name capabilities
only; this module registers and executes nothing.
"""

from contracts.harness import BusinessNode, ChannelEntryBrief, ProductionGraph, ScenarioComposition
from harness.clarity import require_approved


def _node(node_id, objective, inputs, outputs, skills, gate, handoffs, rework, tools, human=False):
    return BusinessNode(
        node_id=node_id, objective=objective, input_contracts=inputs, output_contracts=outputs,
        agent_role=f"{node_id}_analyst", skill_group=skills, quality_gate=gate,
        handoff_options=handoffs, rework_node=rework, allowed_tools=tools,
        coverage_standards=("输入契约完整", "结论引用证据或明确标为假设", "输出契约完整"),
        human_review=human,
    )


WORKSHOPS = (
    _node("scope_clarity", "把需求澄清成可执行 brief", ("ManagerRequest",), ("ChannelEntryBrief",),
          ("需求澄清", "边界识别", "交付定义"), "G0_BRIEF", ("scene_channel",), "scope_clarity", ()),
    _node("scene_channel", "产品场景对应渠道业态与优先级", ("ChannelEntryBrief", "ProductProfile"), ("TargetChannels",),
          ("场景映射", "业态归类"), "G1_CHANNEL", ("territory",), "scene_channel", ("profile_query",)),
    _node("territory", "选出先打的商圈", ("TargetChannels", "TerritoryData"), ("TerritoryRanking",),
          ("商圈判读", "需求密度分析"), "G2_TERRITORY", ("competition",), "scene_channel", ("grid_statistics",), True),
    _node("competition", "判断竞争态势与切入策略", ("TerritoryRanking", "CompetitionAvailabilityData"), ("CompetitionLandscape",),
          ("竞争分析",), "G3_COMPETITION", ("terminal",), "territory", ("competition_scan",)),
    _node("terminal", "优选终端、分级与下一步动作", ("TerritoryRanking", "CompetitionLandscape", "MerchantData"), ("TerminalList",),
          ("终端优选", "疑难商户归类"), "G4_TERMINAL", ("distribution",), "territory", ("merchant_query", "scoring"), True),
    _node("distribution", "明确供货伙伴和招商缺口", ("TerminalList", "PartnerData"), ("PartnerPlan",),
          ("伙伴筛选", "覆盖评估"), "G5_PARTNER", ("outreach",), "terminal", ("partner_coverage",)),
    _node("outreach", "确定触达话术和进店物料", ("TargetChannels", "EntryStrategy", "ProductProfile"), ("OutreachMaterials",),
          ("老板视角话术", "合规改写"), "G6_COMPLIANCE", ("delivery",), "outreach", ("compliance_scan",)),
    _node("delivery", "汇总作战包、决策书与任务", ("ApprovedArtifacts",), ("DeliveryPackage", "DecisionBook", "FieldTasks"),
          ("作战包汇总", "决策书撰写"), "G7_DELIVERY", ("field_feedback",), "outreach", ("export",)),
    _node("field_feedback", "回收拜访结果并修正判断", ("VisitRecords",), ("Review", "ParameterSuggestions"),
          ("复盘", "策略修正"), "G8_FEEDBACK", ("territory", "terminal"), None, ("visit_statistics",)),
)
PRODUCTION_GRAPH = ProductionGraph(entry_node="scope_clarity", nodes=WORKSHOPS)

# Bible section 04 selects the nodes; skipped-node rework/input substitutions are
# explicit P2 configuration choices. No nearest-node or runtime routing inference.
SCENARIOS = {
    "restaurant_entry": {
        "nodes": ("scope_clarity", "scene_channel", "territory", "competition", "terminal", "distribution", "outreach", "delivery"),
        "rework": {}, "inputs": {},
        "deliverables": ("铺市作战包", "市场进入决策书"),
    },
    "distributor_recruitment": {
        "nodes": ("scope_clarity", "territory", "distribution", "outreach", "delivery"),
        "rework": {"territory": "scope_clarity", "distribution": "territory"},
        "inputs": {"territory": ("ChannelEntryBrief", "TerritoryData"),
                   "distribution": ("TerritoryRanking", "PartnerData"),
                   "outreach": ("ChannelEntryBrief", "PartnerPlan", "ProductProfile")},
        "deliverables": ("经销商招募清单", "联络话术"),
    },
    "competition_inspection": {
        "nodes": ("scope_clarity", "competition", "delivery"),
        "rework": {"competition": "scope_clarity", "delivery": "competition"},
        "inputs": {"competition": ("ChannelEntryBrief", "CompetitionAvailabilityData")},
        "deliverables": ("竞争态势简报",),
    },
}


def compose(brief: ChannelEntryBrief, *, latest: ChannelEntryBrief | None = None) -> ScenarioComposition:
    require_approved(brief, latest=latest)
    config = SCENARIOS[brief.delivery_profile]
    ids = config["nodes"]
    nodes = []
    for index, node_id in enumerate(ids):
        base = PRODUCTION_GRAPH.node(node_id)
        values = {**base.model_dump(), "handoff_options": ids[index + 1:index + 2],
                  "rework_node": config["rework"].get(node_id, base.rework_node),
                  "input_contracts": config["inputs"].get(node_id, base.input_contracts)}
        if node_id == "delivery":
            values["output_contracts"] = config["deliverables"]
        nodes.append(BusinessNode.model_validate(values))
    return ScenarioComposition(
        project_id=brief.project_id, brief_id=brief.brief_id, brief_version=brief.version,
        delivery_profile=brief.delivery_profile,
        graph=ProductionGraph(entry_node=ids[0], nodes=tuple(nodes)),
        deliverables=config["deliverables"],
        configuration_source="Product Bible v1.0 sections 04/05; P2 explicit scenario routing v1",
    )
