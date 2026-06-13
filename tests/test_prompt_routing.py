import os

from core.prompt_routing import (
    brain_fast_chitchat_eligible,
    format_intent_routing_user_addon,
    infer_assistant_expects_reply,
    is_pure_chitchat_private,
    private_dm_chitchat_continuity_override,
    recent_dialogue_hints_hygiene_packaging,
    text_looks_continuation_cue,
    text_looks_dialog_followup_cue,
    text_looks_hygiene_packaging_consumer,
    text_looks_minimal_reaction,
    text_warrants_textbook_rag,
    user_requests_dialogue_analysis,
    user_requests_dialogue_analysis_effective,
    user_requests_verbatim_group_relay,
)
from core.brain.text_helpers import user_provided_ordered_checklist


def test_is_pure_chitchat_private_greetings():
    assert is_pure_chitchat_private("привет")
    assert is_pure_chitchat_private("как дела")
    assert is_pure_chitchat_private("как жела")
    assert is_pure_chitchat_private("что нового")
    assert is_pure_chitchat_private("что нового?")
    assert is_pure_chitchat_private("Спасибо!")
    assert is_pure_chitchat_private("окей")
    assert not is_pure_chitchat_private("")
    assert not is_pure_chitchat_private("объясни интегралы на завтрашний экзамен")


def test_brain_fast_chitchat_eligible_private_and_group():
    assert brain_fast_chitchat_eligible("привет", None, None, None, None)
    assert brain_fast_chitchat_eligible("как жела", None, None, None, None)
    assert brain_fast_chitchat_eligible("что нового", None, None, None, None)
    assert brain_fast_chitchat_eligible("привет", "g1", None, None, None)
    assert not brain_fast_chitchat_eligible("привет", None, {"local_path": "/tmp/x.png"}, None, None)
    assert not brain_fast_chitchat_eligible("привет", None, None, {"x": 1}, None)
    assert not brain_fast_chitchat_eligible("привет", None, None, None, {"y": 1})


def test_infer_assistant_expects_reply():
    assert infer_assistant_expects_reply("Какой вариант верный?")
    assert not infer_assistant_expects_reply("Ок.")
    assert infer_assistant_expects_reply("x" * 430)
    assert infer_assistant_expects_reply("x" * 250, task_tier="deep", last_intent="general")
    assert infer_assistant_expects_reply("x" * 170, last_intent="explain")
    assert infer_assistant_expects_reply(
        "Пожалуйста, уточните, что именно вы хотите обсудить из статьи про ИИ.",
        last_intent="explain",
    )


def test_private_dm_chitchat_continuity_override():
    ds = {"assistant_expects_reply": True}
    assert private_dm_chitchat_continuity_override(None, ds, "ок")
    assert not private_dm_chitchat_continuity_override(None, ds, "как дела")
    assert not private_dm_chitchat_continuity_override(None, ds, "привет")
    assert not private_dm_chitchat_continuity_override(None, ds, "спасибо")
    assert not private_dm_chitchat_continuity_override("g1", ds, "ок")
    assert not private_dm_chitchat_continuity_override(None, {}, "ок")
    os.environ["BRAIN_PRIVATE_DM_CHITCHAT_CONTINUITY_GUARD"] = "false"
    try:
        assert not private_dm_chitchat_continuity_override(None, ds, "ок")
    finally:
        os.environ.pop("BRAIN_PRIVATE_DM_CHITCHAT_CONTINUITY_GUARD", None)


def test_text_warrants_textbook_rag():
    assert text_warrants_textbook_rag("реши задачу 5 по математике")
    assert text_warrants_textbook_rag("дз стр. 10 упр 2")
    assert not text_warrants_textbook_rag("как дела")


def test_format_intent_routing_non_empty():
    s = format_intent_routing_user_addon("спасибо большое", for_group=False)
    assert "лёгкий диалог" in s
    g = format_intent_routing_user_addon("всем привет", for_group=True)
    assert "групп" in g.lower() or "Групп" in g


def test_user_requests_dialogue_analysis():
    assert user_requests_dialogue_analysis("сделай полный анализ разговора")
    assert user_requests_dialogue_analysis("проанализируй диалог, ты не учишься на ошибках")
    assert user_requests_dialogue_analysis("посмотри назад и сделай выводы")
    assert user_requests_dialogue_analysis("проверь переписку, кто прав")
    assert user_requests_dialogue_analysis("найди истину в переписке")
    assert user_requests_dialogue_analysis("review the chat and summarize")
    assert not user_requests_dialogue_analysis("какая погода")
    assert not user_requests_dialogue_analysis("анализ системы без лишних слов")


def test_user_requests_dialogue_analysis_effective_meta():
    ctx = {"meta_intent": {"meta": "dialogue_review", "confidence": 0.8}}
    os.environ["META_INTENT_MIN_CONFIDENCE"] = "0.5"
    assert user_requests_dialogue_analysis_effective("случайная фраза без ключевых слов", ctx)
    assert not user_requests_dialogue_analysis_effective("случайная фраза без ключевых слов", {})


def test_format_intent_dialogue_analysis_addon():
    s = format_intent_routing_user_addon("сделай полный анализ нашего диалога", for_group=False)
    assert "SelfProgramming" in s
    assert "recent_dialogue" in s


def test_text_looks_hygiene_packaging_consumer():
    assert text_looks_hygiene_packaging_consumer(
        "Какие ежедневки для женщин лучше, состав, маркировка? Цифра 4 в треугольнике на упаковке?"
    )
    assert not text_looks_hygiene_packaging_consumer("карьера для женщин в IT")


def test_format_intent_hygiene_addon():
    s = format_intent_routing_user_addon(
        "ежедневки прокладки состав треугольник на упаковке", for_group=False
    )
    assert "ежедневные прокладки" in s or "вкладыш" in s
    assert "SelfProgramming" in s or "UniversalSearch" in s


def test_continuation_cue_and_hygiene_from_history():
    assert text_looks_continuation_cue("Продолжай")
    assert text_looks_continuation_cue("дальше.")
    hist = [
        {"role": "user", "text": "Какие ежедневки лучше и что значит 4 в треугольнике?"},
        {"role": "assistant", "text": "Про спорт..."},
    ]
    assert recent_dialogue_hints_hygiene_packaging(hist)
    s = format_intent_routing_user_addon("Продолжай", for_group=False, recent_dialogue=hist)
    assert "продолжить" in s.lower() or "продолж" in s.lower() or "короткая реплика" in s.lower()
    assert "птиц" in s.lower() or "recent_dialogue" in s.lower()
    assert "ежедневные прокладки" in s or "вкладыш" in s


def test_minimal_reaction_and_followup_cue():
    assert text_looks_minimal_reaction("!")
    assert text_looks_minimal_reaction("?!")
    assert text_looks_dialog_followup_cue("!")
    assert not text_looks_dialog_followup_cue("привет")
    hist = [{"role": "user", "text": "С пересыланием не работает, да?"}]
    s = format_intent_routing_user_addon("!", for_group=False, recent_dialogue=hist)
    assert "не понял" in s.lower()
    assert "recent_dialogue" in s.lower()


def test_verbatim_group_relay_intent():
    t = 'Напиши тут "@example_bot привет" поговорим через @id'
    assert user_requests_verbatim_group_relay(t)
    s = format_intent_routing_user_addon(t, for_group=True)
    assert "дословн" in s.lower()
    assert "найти пользователя" in s.lower()
    assert not user_requests_verbatim_group_relay("просто привет всем")


def test_ordered_checklist_detector_distinguishes_single_question_mode():
    checklist = (
        "1) Контекст\nКоротко перескажи.\n"
        "2) Память\nЗапомни число 47.\n"
        "3) Проверка\nНазови число.\n"
    )
    single_question = (
        "Сформулируй, пожалуйста, что именно мне нужно уточнить, чтобы ты мог дать строгое математическое решение. "
        "Не решай задачу до уточнения, а только задай один точный вопрос."
    )
    assert user_provided_ordered_checklist(checklist)
    assert not user_provided_ordered_checklist(single_question)
