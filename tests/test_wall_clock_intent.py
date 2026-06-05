import unittest

from core.timezone_inference import looks_like_wall_clock_question


class WallClockIntentTests(unittest.TestCase):
    def test_positive_phrases(self):
        self.assertTrue(looks_like_wall_clock_question("который час?"))
        self.assertTrue(looks_like_wall_clock_question("Который сейчас час?"))
        self.assertTrue(looks_like_wall_clock_question("What time is it"))
        self.assertTrue(looks_like_wall_clock_question("сколько сейчас часов"))
        self.assertTrue(looks_like_wall_clock_question("текущее время в минске"))

    def test_no_false_positive_chasovoy(self):
        self.assertFalse(
            looks_like_wall_clock_question(
                "укажи часовой пояс Europe/Minsk и повтори ответ"
            )
        )
        self.assertFalse(
            looks_like_wall_clock_question(
                "Проблема в жёстком лимите _clip, обрезает на полуслове"
            )
        )

    def test_no_false_positive_sometimes(self):
        self.assertFalse(looks_like_wall_clock_question("Sometimes I use the word time casually"))

    def test_no_false_positive_skolko_chastey(self):
        self.assertFalse(looks_like_wall_clock_question("сколько частей в документе"))

    def test_no_false_positive_v_tekushchee_vremya(self):
        self.assertFalse(
            looks_like_wall_clock_question("В текущее время запас кислорода на 48 часов")
        )
        self.assertFalse(looks_like_wall_clock_question("в текущее время ситуация критическая"))

    def test_no_false_positive_scenario_date_phrases(self):
        self.assertFalse(looks_like_wall_clock_question("сегодня дата запуска плана"))
        self.assertFalse(looks_like_wall_clock_question("дата и время прилёта челнока"))

    def test_no_false_positive_english_current_time_idiom(self):
        self.assertFalse(looks_like_wall_clock_question("current time to die"))

    def test_kakaya_data_i_vremya(self):
        self.assertTrue(looks_like_wall_clock_question("какая дата и время сейчас"))

    def test_skolko_vremya_informal(self):
        self.assertTrue(looks_like_wall_clock_question("Сколько время?"))
        self.assertTrue(looks_like_wall_clock_question("Какое у меня локальное время сейчас?"))

    def test_no_false_positive_letter_count(self):
        self.assertFalse(looks_like_wall_clock_question("сколько букв «Р» в слове Google"))


if __name__ == "__main__":
    unittest.main()
