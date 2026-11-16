import re
from typing import Any, Dict, List, Optional, Tuple

import pymorphy3
import yaml
from Levenshtein import distance

from src.constants import (
    DUMMY_NAME_FORMS_FEMALE,
    DUMMY_NAME_FORMS_MALE,
    DUMMY_PERSONAL_INFO_FEMALE,
    DUMMY_PERSONAL_INFO_MALE,
)
from src.job_manager.playwright_manager import PlaywrightJobManager
from src.logger_config import logger
from src.utils.utils import load_app_config
from src.utils.json_to_readable import transform_resume_data

config = load_app_config()
ANONYMIZE = config.get("ANONYMIZE", True)
GITHUB_LINK_PATTERN = r"https?://(?:www\.)?github\.com/([\w-]+)"
MORPH = pymorphy3.MorphAnalyzer()
CASES = ["nomn", "gent", "datv", "accs", "ablt", "loct"]
# граммемы pymorphy3 для частей ФИО
NAME_GRAMMEMES = {"first_name": "Name", "middle_name": "Patr", "last_name": "Surn"}


def _decline_name(key: str, value: str, sex: Optional[str]) -> Dict[str, str]:
    """Падежные формы части ФИО. Если pymorphy3 не распознал слово — только исходная форма"""
    gender = "femn" if (sex or "").lower() == "женский" else "masc"
    parses = [
        p for p in MORPH.parse(value) if NAME_GRAMMEMES[key] in p.tag and p.tag.case == "nomn"
    ]
    if not parses:
        return {"nomn": value}
    parse = next((p for p in parses if p.tag.gender == gender), parses[0])
    forms = {}
    for case in CASES:
        inflected = parse.inflect({case, "sing"})
        forms[case] = inflected.word.title() if inflected else value
    forms["nomn"] = value
    return forms


def _fix_dummy_names(text: str, dummy_name_forms: Dict[str, Dict[str, str]]) -> str:
    """Исправляет искажения ФИО-пустышки в ответе LLM: ударения и опечатки (Звягольцев)"""
    # ударения (комбинируемые акут и гравис) в тексте письма не нужны в любом случае
    text = text.replace("\u0301", "").replace("\u0300", "")
    # список, а не множество: при равном расстоянии выбирается форма, идущая раньше по падежам
    dummy_forms = list(
        dict.fromkeys(form for forms in dummy_name_forms.values() for form in forms.values())
    )

    def fix_word(match: re.Match) -> str:
        word = match.group()
        if word in dummy_forms:
            return word
        best_form = min(dummy_forms, key=lambda form: distance(word.lower(), form.lower()))
        # допускаем 1 опечатку для коротких форм и 2 — для длинных
        if distance(word.lower(), best_form.lower()) <= max(1, len(best_form) // 5):
            return best_form
        return word

    return re.sub(r"[А-Яа-яЁё]+", fix_word, text)


def _telegram_username(value: str) -> Optional[str]:
    """Ник из значения Telegram (ссылка, @ник или ник). None, если это не ник, например телефон"""
    match = re.fullmatch(
        r"(?:(?:https?://)?(?:www\.)?(?:t|telegram)\.me/|@)?([A-Za-z]\w{4,31})/?", value.strip()
    )
    return match.group(1) if match else None


def _value_pattern(key: str, value: str) -> str:
    """Регулярка для поиска значения в тексте: телефон ищется по цифрам, остальное — целиком"""
    digits = re.sub(r"\D", "", value)
    if key == "phone" and digits:
        # +7 и 8 в начале российского номера взаимозаменяемы
        if len(digits) == 11 and digits[0] in "78":
            prefix, digits = r"(?:\+7|8)", digits[1:]
        else:
            prefix = r"\+?"
        return rf"(?<!\d){prefix}[\s()-]*" + r"[\s()-]*".join(digits) + r"(?!\d)"
    # \b не срабатывает на краях значений вида "@nick" или "https://site.ru/"
    return rf"(?<!\w){re.escape(value)}(?!\w)"


class ResumeScraper:
    def __init__(
        self,
        manager: PlaywrightJobManager,
        job_title: str,
        resume_id: str,
        gpt_answerer_component: Any,
    ):
        self.manager = manager
        self.job_title = job_title
        self.resume_info = {}
        self.resume_id = resume_id
        self.personal_information = {}
        self.resume_info["general_knowledge_questions"] = ""
        self.github_links = []
        self.gpt_answerer_component = gpt_answerer_component

    async def get_resume_parameters(self) -> Tuple[str, List[str]]:
        """Получить ID нужного резюме"""
        return await self.get_id_of_selected_resume()

    async def get_id_of_selected_resume(self) -> Tuple[str, List[str]]:
        """Получить ID нужного резюме"""
        response = await self.manager.get_my_resumes_from_browser()
        resumes = response.get("items", [])
        if not resumes:
            raise ValueError("Список резюме пуст: не удалось получить резюме из профиля hh.ru")
        # найти среди резюме наиболее схожее по названию с должностью, что указана в настройках
        resume_titles = [r["title"] if r["title"] else "" for r in resumes]
        # если не задана должность - возвращаем первое резюме
        if not self.job_title:
            # если в параметрах поиска есть resume_id - используем резюме с этим id
            if self.resume_id:
                for i, resume in enumerate(resumes):
                    if resume["id"] == self.resume_id:
                        self.job_title = resume_titles[i]
                        return resume["id"], resume_titles
            self.resume_id = resumes[0]["id"]
            self.job_title = resume_titles[0]
            return resumes[0]["id"], resume_titles
        distances = [
            (i, distance(self.job_title.lower(), title.lower()))
            for i, title in enumerate(resume_titles)
        ]
        best_titile_idx = min(distances, key=lambda x: x[1])[0]
        best_match_resume = resumes[best_titile_idx]
        best_match_resume_title = resume_titles[best_titile_idx]
        resume_id = best_match_resume["id"]
        logger.info(
            f"Найден наиболее подходящий вариант резюме для должности {self.job_title}: {best_match_resume_title}"
        )
        self.job_title = best_match_resume_title
        self.resume_id = resume_id
        return resume_id, resume_titles

    async def get_resume_info(self) -> Tuple[str, Dict[str, Any]]:
        """Собрать всю информацию о резюме пользователя"""
        self.resume_info = await self.get_selected_resume_info(self.resume_id)
        self.get_previous_job_details()
        # если можно начинать поиск - парсим дополнительные данные о контактах из резюме с помощью LLM
        personal_information = self.resume_info["personal_information"]
        for key in ["telegram", "whatsapp", "phone", "email", "linkedin"]:
            if not personal_information.get(key):
                self.parse_contacts(self.resume_info.get("about_me"))
                break
        self.personal_information = self.resume_info["personal_information"].copy()
        self.anonymize_personal_information()
        self.save_resume_info()
        resume_readable = transform_resume_data(self.resume_info)
        resume_readable = self.anonymize_text(resume_readable)
        return self.resume_info, resume_readable

    async def get_selected_resume_info(self, resume_id: str) -> Dict[str, Any]:
        """Получить информацию о нужном резюме"""
        resume_info = await self.manager.get_resume_content_from_browser(resume_id)
        return resume_info

    def get_previous_job_details(self) -> None:
        """Добавить информацию о предыдущей работе"""
        if not self.resume_info["experience"]:
            return
        self.resume_info["previous_job_details"] = {}
        self.resume_info["previous_job_details"]["why_leave_previous_job"] = (
            "На предыдущей работе не устраивало отсутствие карьерного роста и интересных задач."
        )
        self.resume_info["previous_job_details"]["team"] = (
            "Коллектив на предыдущей работе был дружный, не токсичный."
        )
        self.resume_info["previous_job_details"]["boss"] = (
            "Отношения с начальством были хорошие, токсичного поведения замечено не было."
        )

    def parse_contacts(self, resume_info: str) -> None:
        """Парсим контакты из резюме"""
        logger.info("Парсим контакты из резюме")
        contacts = self.gpt_answerer_component.parse_contacts(resume_info)
        personal_information = self.resume_info["personal_information"]
        for key in ["phone", "email", "linkedin", "telegram", "whatsapp"]:
            value = contacts.get(key)
            # не перезаписываем контакты, полученные с hh.ru
            if value and value != "No info" and not personal_information.get(key):
                personal_information[key] = value

    def save_resume_info(self) -> None:
        """Сохранить резюме в файл"""
        with open("data_folder/output/resume.yaml", "w", encoding="utf-8") as f:
            yaml.dump(self.resume_info, f, allow_unicode=True, default_flow_style=False)

    def anonymize_personal_information(self) -> None:
        """Анонимазовать персональные данные путем подмены их на данные-пустышки"""
        if not ANONYMIZE:
            return
        sex = self.personal_information.get("sex")
        if (sex or "").lower() == "женский":
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_FEMALE
        else:
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_MALE
        # анонимизировать графы "персональная информация" и "обо мне"
        for key, value in dummy_pesonal_info.items():
            if self.resume_info["personal_information"].get(key):
                self.resume_info["personal_information"][key] = value

    def _replace_name_forms(
        self, text: str, dummy_name_forms: Dict[str, Dict[str, str]], deanonymize: bool
    ) -> str:
        """Заменяет ФИО во всех падежах на ФИО-пустышку в том же падеже (или обратно)"""
        sex = self.personal_information.get("sex")
        mapping = {}
        for key, dummy_forms in dummy_name_forms.items():
            real_value = self.personal_information.get(key)
            if not real_value:
                continue
            real_forms = _decline_name(key, real_value, sex)
            for case, dummy_form in dummy_forms.items():
                # нераспознанное pymorphy3 слово подставляем в исходной форме во всех падежах
                real_form = real_forms.get(case, real_value)
                if deanonymize:
                    mapping.setdefault(dummy_form, real_form)
                else:
                    mapping.setdefault(real_form, dummy_form)
                    # в тексте "ё" часто пишут как "е"
                    mapping.setdefault(real_form.replace("ё", "е"), dummy_form)
        if not mapping:
            return text
        # одна регулярка на все формы, чтобы результат одной замены не попал под следующую
        alternatives = "|".join(re.escape(form) for form in sorted(mapping, key=len, reverse=True))
        return re.sub(rf"(?<!\w)(?:{alternatives})(?!\w)", lambda m: mapping[m.group()], text)

    def _replace_telegram(self, text: str, dummy_telegram: str, deanonymize: bool) -> str:
        """Заменяет ник Telegram в любом формате (ссылка, @ник, ник) на ник-пустышку или обратно"""
        real_telegram = self.personal_information.get("telegram")
        if not real_telegram:
            return text
        dummy_username = _telegram_username(dummy_telegram)
        real_username = _telegram_username(real_telegram)
        if real_username is None:
            # в поле Telegram не ник (например, телефон) — заменяем значение целиком
            if not deanonymize:
                return re.sub(_value_pattern("telegram", real_telegram), dummy_telegram, text)
            dummy_mention = (
                rf"(?<![\w/.@])(?:(?:https?://)?(?:www\.)?(?:t|telegram)\.me/|@)?"
                rf"{dummy_username}(?!\w)"
            )
            return re.sub(dummy_mention, lambda _: real_telegram, text, flags=re.IGNORECASE)
        # меняем только сам ник, чтобы сохранить формат, в котором он записан в тексте
        if deanonymize:
            source, target = dummy_username, real_username
        else:
            source, target = real_username, dummy_username
        return re.sub(
            rf"(?<!\w){re.escape(source)}(?!\w)", lambda _: target, text, flags=re.IGNORECASE
        )

    def anonymize_text(self, input_: str) -> str:
        """Анонимизировать текст резюме: заменить персональные данные на данные-пустышки"""
        if not ANONYMIZE:
            return input_
        sex = self.resume_info["personal_information"].get("sex")
        if (sex or "").lower() == "женский":
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_FEMALE
            dummy_name_forms = DUMMY_NAME_FORMS_FEMALE
        else:
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_MALE
            dummy_name_forms = DUMMY_NAME_FORMS_MALE
        input_ = self._replace_name_forms(input_, dummy_name_forms, deanonymize=False)
        for key, value in dummy_pesonal_info.items():
            # анонимизируем имя пользователя в ссылке на github
            if key == "github":
                self.github_links = re.findall(GITHUB_LINK_PATTERN, input_)
                self.github_links = [
                    f"https://github.com/{github_link}" for github_link in self.github_links
                ]
                if self.github_links:
                    input_ = re.sub(GITHUB_LINK_PATTERN, value, input_)
            elif key in [*dummy_name_forms, "telegram"]:
                continue
            else:
                if not self.resume_info["personal_information"].get(key):
                    continue
                value_to_replace = self.personal_information[key]
                if "github" in value_to_replace:
                    continue
                input_ = re.sub(_value_pattern(key, value_to_replace), value, input_)
        # ник Telegram заменяем последним, чтобы не задеть email и ссылки с тем же ником
        input_ = self._replace_telegram(input_, dummy_pesonal_info["telegram"], deanonymize=False)
        return input_

    def deanonymize_personal_information(self, output: str) -> str:
        """Деанонимазовать данные в ответе"""
        if not ANONYMIZE:
            return output
        sex = self.personal_information.get("sex")
        if (sex or "").lower() == "женский":
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_FEMALE
            dummy_name_forms = DUMMY_NAME_FORMS_FEMALE
        else:
            dummy_pesonal_info = DUMMY_PERSONAL_INFO_MALE
            dummy_name_forms = DUMMY_NAME_FORMS_MALE
        output = _fix_dummy_names(output, dummy_name_forms)
        output = self._replace_name_forms(output, dummy_name_forms, deanonymize=True)
        for key, value_to_replace in dummy_pesonal_info.items():
            if key in [*dummy_name_forms, "telegram"]:
                continue
            if key == "github":
                if not self.github_links:
                    continue
                # заменяем только ссылки-пустышки, по порядку их появления в тексте
                github_links = iter(self.github_links)
                output = re.sub(
                    rf"{re.escape(value_to_replace)}(?![\w-])",
                    lambda _: next(github_links, self.github_links[-1]),
                    output,
                )
            else:
                if "github" in value_to_replace:
                    continue
                if not self.personal_information.get(key):
                    continue
                value = self.personal_information[key]
                # функция вместо строки, чтобы обратные слеши в реальном значении не трактовались re.sub
                output = re.sub(_value_pattern(key, value_to_replace), lambda _: value, output)
        output = self._replace_telegram(output, dummy_pesonal_info["telegram"], deanonymize=True)
        return output
