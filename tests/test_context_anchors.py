"""Tests for context_anchors — entity extraction, coreference, anchor store."""

import unittest
from core.brain.context_anchors import (
    _is_likely_pronoun_anaphora,
    has_anaphora,
    needs_anchors,
    get_entities_for_prompt,
    extract_entities_from_dialogue,
    _extract_entities_from_one_text,
    update_anchor_store,
    build_context_anchors_block,
)


class TestCoreference(unittest.TestCase):
    """Tests for coreference detection (has_anaphora — any position)."""

    def test_start_pronoun(self):
        self.assertTrue(has_anaphora("Его потом пустят в евросоюз"))
        self.assertTrue(has_anaphora("Он придёт завтра"))
        self.assertTrue(has_anaphora("После его высказывания"))
        self.assertTrue(has_anaphora("Про это я и говорю"))

    def test_mid_anaphora(self):
        """Coreference ВНУТРИ текста — то, что не ловил старый regex."""
        self.assertTrue(has_anaphora("А что по поводу его высказывания про фейерверки?"))
        self.assertTrue(has_anaphora("Ты согласен с ним насчёт налогов?"))
        self.assertTrue(has_anaphora("Мне кажется, она права в этом вопросе"))
        self.assertTrue(has_anaphora("После его заявления я задумался"))
        self.assertTrue(has_anaphora("Они сказали, что это не так"))

    def test_non_anaphora(self):
        self.assertFalse(has_anaphora("Павел Дуров сообщил"))
        self.assertFalse(has_anaphora("Какая сегодня погода"))
        self.assertFalse(has_anaphora("Привет, как дела"))
        self.assertFalse(has_anaphora(""))
        self.assertFalse(has_anaphora("аа"))  # слишком коротко


class TestAnaphoraDetect(unittest.TestCase):
    """Tests for start-position anaphora detector."""

    def test_pronoun_start(self):
        self.assertTrue(_is_likely_pronoun_anaphora("Его потом пустят в евросоюз"))
        self.assertTrue(_is_likely_pronoun_anaphora("Он придёт завтра"))
        self.assertTrue(_is_likely_pronoun_anaphora("Они уже ушли"))
        self.assertTrue(_is_likely_pronoun_anaphora("После его высказывания"))
        self.assertTrue(_is_likely_pronoun_anaphora("Про это я и говорю"))

    def test_non_anaphora(self):
        self.assertFalse(_is_likely_pronoun_anaphora("Павел Дуров сообщил"))
        self.assertFalse(_is_likely_pronoun_anaphora("Какая сегодня погода"))
        self.assertFalse(_is_likely_pronoun_anaphora(""))

    def test_short_ref_words(self):
        self.assertTrue(_is_likely_pronoun_anaphora("Ну и он такой"))
        self.assertTrue(_is_likely_pronoun_anaphora("А ему что?"))


class TestNeedsAnchors(unittest.TestCase):
    """Tests for the master predicate that decides if anchors are needed."""

    def test_anaphora_start_triggers(self):
        self.assertTrue(needs_anchors("Его потом пустят", [
            {"role": "user", "text": "Павел Дуров сказал привет"},
        ]))

    def test_mid_anaphora_triggers(self):
        self.assertTrue(needs_anchors("А что он думает про это?", [
            {"role": "user", "text": "Павел Дуров сказал привет"},
        ]))

    def test_entities_in_recent_triggers(self):
        """Даже без анафоры, если есть именованные сущности — показываем."""
        self.assertTrue(needs_anchors("Расскажи подробнее", [
            {"role": "user", "text": "Павел Дуров сказал привет"},
            {"role": "assistant", "text": "Дуров известен Telegram"},
        ]))

    def test_no_entities_no_anaphora(self):
        self.assertFalse(needs_anchors("привет как дела", [
            {"role": "user", "text": "нормально"},
        ]))

    def test_empty_input(self):
        self.assertFalse(needs_anchors("", []))
        self.assertFalse(needs_anchors("", [{"role": "user", "text": "а"}]))


class TestEntityExtraction(unittest.TestCase):
    """Tests for entity extraction from text and dialogue."""

    def test_extract_from_one_text(self):
        entities = _extract_entities_from_one_text(
            "Павел Дуров сообщил, что соскучился по иранским фейерверкам в Дубае"
        )
        # "Павел" — первое слово, без повторения не считается
        # "Дуров" — в середине, считается
        self.assertIn("Дуров", entities)
        # "Дубае" — в середине, считается
        self.assertIn("Дубае", entities)

    def test_extract_from_two_texts(self):
        """При повторении первого слова — оно тоже сущность."""
        entities = _extract_entities_from_one_text(
            "Павел Дуров сказал привет. Павел любит Дубай."
        )
        self.assertIn("Павел", entities)  # первый, но появляется >= 2 раз
        self.assertIn("Дуров", entities)
        self.assertIn("Дубай", entities)

    def test_extract_from_dialogue(self):
        dialogue = [
            {"role": "user", "text": "Павел Дуров сообщил новость"},
            {"role": "assistant", "text": "Дуров не впервые делает такие заявления"},
        ]
        entities = extract_entities_from_dialogue(dialogue)
        self.assertIn("Павел", entities)
        self.assertIn("Дуров", entities)

    def test_empty_dialogue(self):
        self.assertEqual(extract_entities_from_dialogue([]), [])

    def test_no_entities(self):
        dialogue = [
            {"role": "user", "text": "привет как дела"},
            {"role": "assistant", "text": "нормально"},
        ]
        self.assertEqual(extract_entities_from_dialogue(dialogue), [])

    def test_skip_common_words(self):
        dialogue = [
            {"role": "user", "text": "Привет Это хорошая идея Вот так"},
        ]
        entities = extract_entities_from_dialogue(dialogue)
        for word in ("Привет", "Это", "Вот"):
            self.assertNotIn(word, entities)


class TestAnchorStore(unittest.TestCase):
    """Tests for persistent entity storage with decay."""

    def test_empty_store(self):
        result = update_anchor_store(
            existing=[],
            user_text="Павел Дуров сказал привет",
            assistant_text="Дуров известен всему миру",
        )
        self.assertIn("Павел", result)
        self.assertIn("Дуров", result)

    def test_no_existing_without_entities(self):
        result = update_anchor_store(
            existing=[],
            user_text="привет",
            assistant_text="нормально",
        )
        self.assertEqual(result, [])

    def test_decay_removes_old(self):
        """Старые сущности с низким весом удаляются."""
        existing = [
            {"entity": "старый", "weight": "0.1"},
        ]
        result = update_anchor_store(
            existing=existing,
            user_text="новый Павел пришёл",
            assistant_text="",
            decay_rate=1.0,  # не затухают
        )
        self.assertIn("Павел", result)
        # "старый" удалён из-за веса < 0.3
        self.assertNotIn("Старый", result)

    def test_reinforce_new_entity(self):
        """Новые сущности из текста добавляются."""
        result = update_anchor_store(
            existing=[],
            user_text="Илон Маск запустил ракету",
            assistant_text="Маск известен SpaceX",
        )
        self.assertIn("Илон", result)
        self.assertIn("Маск", result)

    def test_persist_across_turns(self):
        """После двух ходов сущности сохраняются (с затуханием)."""
        # Ход 1
        store = update_anchor_store(
            existing=[],
            user_text="Павел Дуров сказал привет",
            assistant_text="",
        )
        self.assertIn("Павел", store)
        self.assertIn("Дуров", store)

        # Ход 2 — без новых сущностей
        store2 = update_anchor_store(
            existing=store,
            user_text="А что он думает?",
            assistant_text="Он считает что всё хорошо",
            decay_rate=0.9,
        )
        # Сущности должны выжить после одного хода с decay 0.9
        self.assertIn("Павел", store2)
        self.assertIn("Дуров", store2)


class TestGetEntitiesForPrompt(unittest.TestCase):
    """Tests for entities resolution for prompt block."""

    def test_from_store(self):
        entities = get_entities_for_prompt(
            anchor_entities=["Павел", "Дуров"],
            recent_dialogue=[{"role": "user", "text": "а"}],
        )
        self.assertIn("Павел", entities)
        self.assertIn("Дуров", entities)

    def test_fallback_from_dialogue(self):
        entities = get_entities_for_prompt(
            anchor_entities=[],
            recent_dialogue=[
                {"role": "user", "text": "Павел Дуров сказал"},
            ],
        )
        self.assertIn("Павел", entities)
        self.assertIn("Дуров", entities)

    def test_empty_fallback(self):
        entities = get_entities_for_prompt(
            anchor_entities=[],
            recent_dialogue=[],
        )
        self.assertEqual(entities, [])

    def test_store_has_stop_words_removed(self):
        """Стоп-слова не попадают в выдачу из store."""
        entities = get_entities_for_prompt(
            anchor_entities=["Павел", "Дуров", "Привет", "Ну"],
            recent_dialogue=[],
        )
        self.assertIn("Павел", entities)
        self.assertIn("Дуров", entities)
        self.assertNotIn("Привет", entities)
        self.assertNotIn("Ну", entities)


class TestBuildBlock(unittest.TestCase):
    """Tests for building the prompt block."""

    def test_full_block(self):
        block = build_context_anchors_block(
            entities=["Павел Дуров", "Дубай"],
            last_assistant_excerpt="Павел Дуров не впервые делает...",
            previous_user_excerpt="Его потом пустят в евросоюз",
            user_text="После его высказывания",
        )
        self.assertIn("context_anchors", block)
        self.assertIn("Павел Дуров", block)
        self.assertIn("last_assistant", block)
        self.assertIn("previous_user", block)

    def test_no_entities_no_block(self):
        block = build_context_anchors_block(
            entities=[],
            last_assistant_excerpt="",
            previous_user_excerpt="",
            user_text="",
        )
        self.assertEqual(block, "")

    def test_only_assistant(self):
        block = build_context_anchors_block(
            entities=[],
            last_assistant_excerpt="Последний ответ",
            previous_user_excerpt="",
            user_text="",
        )
        self.assertIn("last_assistant", block)
        self.assertNotIn("context_anchors", block)
        self.assertNotIn("previous_user", block)


if __name__ == "__main__":
    unittest.main()
