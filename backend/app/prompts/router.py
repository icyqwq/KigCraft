from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/prompts", tags=["prompts"])


class RequirementOptionOut(BaseModel):
    id: str
    group: str
    label: str
    description: str
    prompt_text: str
    sort_order: int


class PromptChipOut(BaseModel):
    id: str
    category: str
    label: str
    text: str
    sort_order: int


DEFAULT_REQUIREMENTS = [
    RequirementOptionOut(
        id="more_youthful",
        group="脸部风格",
        label="更幼态",
        description="弱化成熟感，让脸部比例更接近幼态头壳。",
        prompt_text="make the face style more youthful and softer while preserving the character identity",
        sort_order=10,
    ),
    RequirementOptionOut(
        id="rounder_face",
        group="脸部风格",
        label="脸更圆",
        description="强化圆润脸型，减少尖锐轮廓。",
        prompt_text="make the face rounder with a fuller cheek shape",
        sort_order=20,
    ),
    RequirementOptionOut(
        id="concentrated_features",
        group="脸部风格",
        label="五官更集中",
        description="让五官分布更紧凑，保留角色识别特征。",
        prompt_text="make the facial features more concentrated and compact while preserving identity",
        sort_order=30,
    ),
    RequirementOptionOut(
        id="smaller_face",
        group="脸部风格",
        label="小脸",
        description="缩小脸部视觉比例，让头壳更可爱。",
        prompt_text="make the face visually smaller and cuter within the head shell proportions",
        sort_order=40,
    ),
    RequirementOptionOut(
        id="shorter_chin",
        group="脸部风格",
        label="下巴更短",
        description="缩短下巴比例，让整体头型更紧凑。",
        prompt_text="make the chin shorter and keep the lower face compact",
        sort_order=50,
    ),
    RequirementOptionOut(
        id="soften_jawline",
        group="脸部风格",
        label="弱化下颌角",
        description="降低下颌角硬朗感，让轮廓更柔和。",
        prompt_text="soften the jaw angles and reduce hard lower-face edges",
        sort_order=60,
    ),
    RequirementOptionOut(
        id="subtle_v_face",
        group="脸部风格",
        label="轻微 V 脸",
        description="加入轻微 V 脸趋势，不改变整体可爱圆润感。",
        prompt_text="add a subtle V-shaped face tendency while keeping the design cute and rounded",
        sort_order=70,
    ),
    RequirementOptionOut(
        id="bigger_eyes",
        group="眼睛",
        label="眼睛更大",
        description="放大眼睛比例，保持角色识别度。",
        prompt_text="make the eyes larger while keeping the original character identity",
        sort_order=80,
    ),
    RequirementOptionOut(
        id="taller_eyes",
        group="眼睛",
        label="眼高增加",
        description="增加眼睛纵向高度，让眼型更饱满。",
        prompt_text="increase the vertical height of the eyes for a fuller eye shape",
        sort_order=90,
    ),
    RequirementOptionOut(
        id="closer_eye_spacing",
        group="眼睛",
        label="眼距略近",
        description="轻微缩短眼距，让五官更集中。",
        prompt_text="move the eyes slightly closer together without distorting the character identity",
        sort_order=100,
    ),
    RequirementOptionOut(
        id="lower_eye_position",
        group="眼睛",
        label="眼位下移",
        description="轻微下移眼位，增强幼态比例。",
        prompt_text="move the eye position slightly lower to emphasize youthful proportions",
        sort_order=110,
    ),
    RequirementOptionOut(
        id="soft_eyes",
        group="眼睛",
        label="眼神更柔和",
        description="降低锐利感，让眼神更温柔。",
        prompt_text="make the eyes softer and gentler",
        sort_order=120,
    ),
    RequirementOptionOut(
        id="keep_eye_color",
        group="眼睛",
        label="保留瞳色",
        description="保留参考图中的瞳色和瞳孔特征。",
        prompt_text="preserve the original iris color and pupil details from the reference",
        sort_order=130,
    ),
    RequirementOptionOut(
        id="keep_expression",
        group="表情",
        label="保留原表情",
        description="尽量延续参考图里的表情特征。",
        prompt_text="preserve the original expression from the reference",
        sort_order=140,
    ),
    RequirementOptionOut(
        id="smile",
        group="表情",
        label="微笑",
        description="加入轻微微笑，不夸张改变角色气质。",
        prompt_text="use a subtle gentle smile",
        sort_order=150,
    ),
    RequirementOptionOut(
        id="neutral_expression",
        group="表情",
        label="无表情",
        description="保持平静无表情，避免明显情绪偏移。",
        prompt_text="use a calm neutral expression without strong emotion",
        sort_order=160,
    ),
    RequirementOptionOut(
        id="pleading",
        group="表情",
        label="委屈",
        description="呈现轻微委屈感，保持可爱和克制。",
        prompt_text="use a slightly pleading expression while keeping it cute and restrained",
        sort_order=170,
    ),
    RequirementOptionOut(
        id="slightly_open_mouth",
        group="表情",
        label="轻微张嘴",
        description="呈现轻微张嘴表情，保持自然克制。",
        prompt_text="use a slightly open mouth expression while keeping it natural and restrained",
        sort_order=180,
    ),
    RequirementOptionOut(
        id="avoid_exaggerated_expression",
        group="表情",
        label="避免夸张表情",
        description="控制表情幅度，避免过度戏剧化。",
        prompt_text="avoid exaggerated expressions and keep the facial emotion subtle",
        sort_order=190,
    ),
    RequirementOptionOut(
        id="keep_bangs",
        group="发型发饰",
        label="保留刘海",
        description="保留参考图中刘海的方向和层次。",
        prompt_text="preserve the bangs shape and direction from the reference",
        sort_order=200,
    ),
    RequirementOptionOut(
        id="hair_strand_layers",
        group="发型发饰",
        label="强调发束层次",
        description="强化发束分组和层次感，便于成品造型识别。",
        prompt_text="emphasize layered hair strand groups and sculptural hair separation",
        sort_order=210,
    ),
    RequirementOptionOut(
        id="keep_accessory",
        group="发型发饰",
        label="保留发饰",
        description="保留参考图中明显的发饰元素。",
        prompt_text="preserve visible hair accessories from the reference",
        sort_order=220,
    ),
    RequirementOptionOut(
        id="keep_ears_horns",
        group="发型发饰",
        label="保留耳朵/角",
        description="保留角色的耳朵、角或类似头部特征。",
        prompt_text="preserve character ears, horns, or similar head features from the reference",
        sort_order=230,
    ),
    RequirementOptionOut(
        id="restore_hair_color",
        group="发型发饰",
        label="还原发色",
        description="还原参考图中的主发色和明暗关系。",
        prompt_text="restore the original hair color and light-dark relationships from the reference",
        sort_order=240,
    ),
    RequirementOptionOut(
        id="keep_highlight",
        group="发型发饰",
        label="保留特殊挑染",
        description="保留发色中的特殊挑染或分区色块。",
        prompt_text="preserve special hair highlights and color blocks from the reference",
        sort_order=250,
    ),
    RequirementOptionOut(
        id="physical_head_shell",
        group="成品质感",
        label="kigurumi 实体头壳",
        description="呈现实物头壳的成品质感。",
        prompt_text="render as a finished physical kigurumi head shell",
        sort_order=260,
    ),
    RequirementOptionOut(
        id="white_studio",
        group="成品质感",
        label="白底棚拍",
        description="使用白底棚拍观感，便于检查造型。",
        prompt_text="use a clean white studio product photo background",
        sort_order=270,
    ),
    RequirementOptionOut(
        id="four_view_final",
        group="成品质感",
        label="四视角成品图",
        description="输出正面、侧面、背面和四分之三视角。",
        prompt_text="output final product views from front, side, back, and three-quarter angles",
        sort_order=290,
    ),
    RequirementOptionOut(
        id="fixed_final_reference_texture",
        group="成品质感",
        label="接近固定成品参考图质感",
        description="让渲染质感接近固定成品参考图，方便和实物预期对齐。",
        prompt_text="match the texture quality of the fixed final product reference as closely as possible",
        sort_order=300,
    ),
]

_REQUIREMENT_ALIASES = {
    "product_four_view": "four_view_final",
}


def resolve_requirement_prompt_texts(requirement_ids: list[str]) -> list[str]:
    requirements_by_id = {requirement.id: requirement for requirement in DEFAULT_REQUIREMENTS}
    prompt_texts: list[str] = []
    for requirement_id in requirement_ids:
        resolved_id = _REQUIREMENT_ALIASES.get(requirement_id, requirement_id)
        requirement = requirements_by_id.get(resolved_id)
        if requirement is not None:
            prompt_texts.append(requirement.prompt_text)
    return prompt_texts


@router.get("/requirements", response_model=list[RequirementOptionOut])
async def list_requirements() -> list[RequirementOptionOut]:
    return DEFAULT_REQUIREMENTS


@router.get("/chips", response_model=list[PromptChipOut])
async def list_chips() -> list[PromptChipOut]:
    return [
        PromptChipOut(
            id=requirement.id,
            category=requirement.group,
            label=requirement.label,
            text=requirement.prompt_text,
            sort_order=requirement.sort_order,
        )
        for requirement in DEFAULT_REQUIREMENTS
    ]
