from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def test_prompt_requirements_return_grouped_readable_options(test_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        response = await client.get("/api/prompts/requirements")

    assert response.status_code == 200
    options = response.json()
    assert options
    assert all(
        {"id", "group", "label", "description", "prompt_text", "sort_order"} <= option.keys()
        for option in options
    )
    assert {option["group"] for option in options} >= {"脸部风格", "眼睛", "表情", "发型发饰", "成品质感"}
    labels = {option["label"] for option in options}
    required_labels = {
        "更幼态",
        "脸更圆",
        "五官更集中",
        "小脸",
        "下巴更短",
        "弱化下颌角",
        "轻微 V 脸",
        "眼睛更大",
        "眼高增加",
        "眼距略近",
        "眼位下移",
        "眼神更柔和",
        "保留瞳色",
        "保留原表情",
        "微笑",
        "无表情",
        "委屈",
        "轻微张嘴",
        "避免夸张表情",
        "保留刘海",
        "强调发束层次",
        "保留发饰",
        "保留耳朵/角",
        "还原发色",
        "保留特殊挑染",
        "kigurumi 实体头壳",
        "白底棚拍",
        "接近固定成品参考图质感",
    }
    assert required_labels <= labels
    assert "支架展示" not in labels
    assert all(isinstance(option["sort_order"], int) for option in options)


async def test_prompt_chips_are_grouped_positive_blocks(test_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        response = await client.get("/prompts/chips")

    assert response.status_code == 200
    chips = response.json()
    assert {chip["category"] for chip in chips} >= {"脸部风格", "眼睛"}
    assert {chip["label"] for chip in chips} >= {
        "五官更集中",
        "保留瞳色",
        "避免夸张表情",
        "强调发束层次",
        "接近固定成品参考图质感",
    }
    forbidden_phrases = [
        "忽略之前",
        "忽略以上",
        "ignore previous",
        "ignore all previous",
        "disregard previous",
        "disregard prior",
        "system prompt",
        "系统提示词",
        "developer message",
        "绕过",
    ]
    chip_text = "\n".join(chip["text"].lower() for chip in chips)
    assert all(phrase not in chip_text for phrase in forbidden_phrases)
