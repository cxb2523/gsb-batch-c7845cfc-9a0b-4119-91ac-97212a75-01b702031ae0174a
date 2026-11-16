from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.constants import DUMMY_PERSONAL_INFO_FEMALE, DUMMY_PERSONAL_INFO_MALE
from src.job_manager.resume_scraper import ResumeScraper


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    manager.get_my_resumes_from_browser = AsyncMock()
    manager.get_resume_content_from_browser = AsyncMock()
    return manager


@pytest.fixture
def mock_gpt():
    return MagicMock()


@pytest.fixture
def mock_resumes_list():
    return {
        "items": [
            {"id": "resume456", "title": "Data Engineer"},
            {"id": "resume123", "title": "Python Developer"},
            {"id": "resume789", "title": "PHP Developer"},
        ]
    }


@pytest.fixture
def mock_resume_data():
    return {
        "personal_information": {
            "first_name": "Иван",
            "last_name": "Иванов",
            "middle_name": "Иванович",
            "sex": "Мужской",
            "phone": "+7(111)222-33-44",
            "email": "ivan.ivanov@email.com",
            "telegram": "https://t.me/real_ivan",
            "whatsapp": "https://wa.me/real_ivan",
            "linkedin": "https://linkedin.com/in/ivan-ivanov",
            "github": "https://github.com/real_ivan",
            "other_site": "https://www.ivan.ru",
        },
        "experience": [
            {
                "position": "Senior Python Developer",
                "company": "Tech Corp",
            }
        ],
        "about_me": "Опытный разработчик.",
        "general_knowledge_questions": "",
    }


class TestInit:
    def test_init_sets_attributes(self, mock_manager, mock_gpt):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", mock_gpt)

        assert scraper.manager is mock_manager
        assert scraper.job_title == "Python Developer"
        assert scraper.resume_id == "abc123"
        assert scraper.gpt_answerer_component is mock_gpt
        assert scraper.resume_info == {"general_knowledge_questions": ""}
        assert scraper.personal_information == {}
        assert scraper.github_links == []


class TestGetIdOfSelectedResume:
    @pytest.mark.asyncio
    async def test_with_job_title_finds_best_match(self, mock_manager, mock_resumes_list):
        mock_manager.get_my_resumes_from_browser.return_value = mock_resumes_list
        scraper = ResumeScraper(mock_manager, "python developer", "", MagicMock())

        resume_id, resume_titles = await scraper.get_id_of_selected_resume()

        mock_manager.get_my_resumes_from_browser.assert_called_once()
        assert resume_id == "resume123"
        assert resume_titles == ["Data Engineer", "Python Developer", "PHP Developer"]

    @pytest.mark.asyncio
    async def test_without_job_title_returns_first(self, mock_manager, mock_resumes_list):
        mock_manager.get_my_resumes_from_browser.return_value = mock_resumes_list
        scraper = ResumeScraper(mock_manager, "", "", MagicMock())

        resume_id, resume_titles = await scraper.get_id_of_selected_resume()

        assert resume_id == "resume456"
        assert scraper.job_title == "Data Engineer"

    @pytest.mark.asyncio
    async def test_without_job_title_with_resume_id_returns_matching(
        self, mock_manager, mock_resumes_list
    ):
        mock_manager.get_my_resumes_from_browser.return_value = mock_resumes_list
        scraper = ResumeScraper(mock_manager, "", "resume789", MagicMock())

        resume_id, resume_titles = await scraper.get_id_of_selected_resume()

        assert resume_id == "resume789"
        assert scraper.job_title == "PHP Developer"

    @pytest.mark.asyncio
    async def test_empty_resumes_list_raises_value_error(self, mock_manager):
        mock_manager.get_my_resumes_from_browser.return_value = {"items": []}
        scraper = ResumeScraper(mock_manager, "Python Developer", "", MagicMock())

        with pytest.raises(ValueError):
            await scraper.get_id_of_selected_resume()


class TestGetResumeParameters:
    @pytest.mark.asyncio
    async def test_delegates_to_get_id_of_selected_resume(self, mock_manager, mock_resumes_list):
        mock_manager.get_my_resumes_from_browser.return_value = mock_resumes_list
        scraper = ResumeScraper(mock_manager, "Python Developer", "", MagicMock())

        with patch.object(
            scraper, "get_id_of_selected_resume", new=AsyncMock(return_value=("id123", ["title"]))
        ) as mock_get_id:
            result = await scraper.get_resume_parameters()

        mock_get_id.assert_called_once()
        assert result == ("id123", ["title"])


class TestGetSelectedResumeInfo:
    @pytest.mark.asyncio
    async def test_calls_manager_with_resume_id(self, mock_manager, mock_resume_data):
        mock_manager.get_resume_content_from_browser.return_value = mock_resume_data
        scraper = ResumeScraper(mock_manager, "Python Developer", "resume123", MagicMock())

        result = await scraper.get_selected_resume_info("resume123")

        mock_manager.get_resume_content_from_browser.assert_called_once_with("resume123")
        assert result == mock_resume_data


class TestGetResumeInfo:
    @pytest.mark.asyncio
    @patch("src.job_manager.resume_scraper.transform_resume_data", return_value="readable text")
    @patch("src.job_manager.resume_scraper.ANONYMIZE", False)
    async def test_full_flow_returns_info_and_readable(
        self, mock_transform, mock_manager, mock_resume_data, mock_gpt
    ):
        mock_manager.get_resume_content_from_browser.return_value = mock_resume_data
        scraper = ResumeScraper(mock_manager, "Python Developer", "resume123", mock_gpt)

        with patch.object(scraper, "save_resume_info"):
            resume_info, resume_readable = await scraper.get_resume_info()

        assert resume_info["personal_information"]["first_name"] == "Иван"
        assert resume_readable == "readable text"
        mock_transform.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.job_manager.resume_scraper.transform_resume_data", return_value="")
    @patch("src.job_manager.resume_scraper.ANONYMIZE", False)
    async def test_calls_parse_contacts_when_phone_missing(
        self, mock_transform, mock_manager, mock_gpt
    ):
        resume_data = {
            "personal_information": {
                "first_name": "Иван",
                "sex": "Мужской",
                "email": "ivan@email.com",
                # phone отсутствует
            },
            "experience": [],
            "about_me": "Текст",
            "general_knowledge_questions": "",
        }
        mock_manager.get_resume_content_from_browser.return_value = resume_data
        mock_gpt.parse_contacts.return_value = {}
        scraper = ResumeScraper(mock_manager, "Python Developer", "resume123", mock_gpt)

        with patch.object(scraper, "save_resume_info"):
            await scraper.get_resume_info()

        mock_gpt.parse_contacts.assert_called_once_with("Текст")


class TestGetPreviousJobDetails:
    def test_adds_details_when_experience_exists(self, mock_manager, mock_resume_data):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.resume_info = mock_resume_data

        scraper.get_previous_job_details()

        assert "previous_job_details" in scraper.resume_info
        assert "why_leave_previous_job" in scraper.resume_info["previous_job_details"]
        assert "team" in scraper.resume_info["previous_job_details"]
        assert "boss" in scraper.resume_info["previous_job_details"]

    def test_skips_when_no_experience(self, mock_manager):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.resume_info = {"experience": [], "general_knowledge_questions": ""}

        scraper.get_previous_job_details()

        assert "previous_job_details" not in scraper.resume_info


class TestParseContacts:
    def test_fills_contacts_from_gpt(self, mock_manager, mock_gpt):
        mock_gpt.parse_contacts.return_value = {
            "phone": "+7(999)999-99-99",
            "email": "test@example.com",
            "telegram": "https://t.me/testuser",
            "linkedin": "https://linkedin.com/in/test",
            "whatsapp": "https://wa.me/testuser",
        }
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", mock_gpt)
        scraper.resume_info["personal_information"] = {}

        scraper.parse_contacts("some resume text")

        pi = scraper.resume_info["personal_information"]
        assert pi["phone"] == "+7(999)999-99-99"
        assert pi["email"] == "test@example.com"
        assert pi["telegram"] == "https://t.me/testuser"
        assert pi["linkedin"] == "https://linkedin.com/in/test"
        assert pi["whatsapp"] == "https://wa.me/testuser"

    def test_skips_empty_contact_fields(self, mock_manager, mock_gpt):
        mock_gpt.parse_contacts.return_value = {"phone": "", "email": "test@email.com"}
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", mock_gpt)
        scraper.resume_info["personal_information"] = {}

        scraper.parse_contacts("resume text")

        assert "phone" not in scraper.resume_info["personal_information"]
        assert scraper.resume_info["personal_information"]["email"] == "test@email.com"


    def test_skips_no_info_and_keeps_existing(self, mock_manager, mock_gpt):
        mock_gpt.parse_contacts.return_value = {
            "phone": "+7(999)999-99-99",
            "email": "No info",
            "telegram": "@testuser",
        }
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", mock_gpt)
        scraper.resume_info["personal_information"] = {"phone": "+7 (111) 222-33-44"}

        scraper.parse_contacts("resume text")

        pi = scraper.resume_info["personal_information"]
        assert pi["phone"] == "+7 (111) 222-33-44"
        assert "email" not in pi
        assert pi["telegram"] == "@testuser"


class TestSaveResumeInfo:
    @patch("builtins.open", new_callable=MagicMock)
    @patch("yaml.dump")
    def test_writes_to_correct_path(self, mock_yaml_dump, mock_open, mock_manager):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.resume_info = {"test": "data"}

        scraper.save_resume_info()

        mock_open.assert_called_once_with("data_folder/output/resume.yaml", "w", encoding="utf-8")
        mock_yaml_dump.assert_called_once()


class TestAnonymizePersonalInformation:
    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_replaces_personal_info_male(self, mock_manager, mock_resume_data):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.resume_info = mock_resume_data
        scraper.personal_information = mock_resume_data["personal_information"].copy()

        scraper.anonymize_personal_information()

        pi = scraper.resume_info["personal_information"]
        assert pi["first_name"] == DUMMY_PERSONAL_INFO_MALE["first_name"]
        assert pi["last_name"] == DUMMY_PERSONAL_INFO_MALE["last_name"]
        assert pi["email"] == DUMMY_PERSONAL_INFO_MALE["email"]

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_replaces_personal_info_female(self, mock_manager):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        female_pi = {
            "first_name": "Мария",
            "last_name": "Петрова",
            "sex": "Женский",
            "email": "maria@email.com",
            "phone": "+7(555)666-77-88",
        }
        scraper.resume_info = {"personal_information": female_pi.copy()}
        scraper.personal_information = female_pi.copy()

        scraper.anonymize_personal_information()

        pi = scraper.resume_info["personal_information"]
        assert pi["first_name"] == DUMMY_PERSONAL_INFO_FEMALE["first_name"]
        assert pi["last_name"] == DUMMY_PERSONAL_INFO_FEMALE["last_name"]
        assert pi["email"] == DUMMY_PERSONAL_INFO_FEMALE["email"]

    @patch("src.job_manager.resume_scraper.ANONYMIZE", False)
    def test_skips_when_anonymize_disabled(self, mock_manager, mock_resume_data):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.resume_info = mock_resume_data
        scraper.personal_information = mock_resume_data["personal_information"].copy()
        original_name = scraper.resume_info["personal_information"]["first_name"]

        scraper.anonymize_personal_information()

        assert scraper.resume_info["personal_information"]["first_name"] == original_name


class TestAnonymizeText:
    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_replaces_pii_and_github_male(self, mock_manager):
        original_info = {
            "first_name": "Иван",
            "last_name": "Иванов",
            "phone": "+7(111)222-33-44",
            "email": "ivan.ivanov@email.com",
            "telegram": "https://t.me/real_ivan",
            "sex": "Мужской",
        }
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = original_info.copy()
        scraper.resume_info["personal_information"] = original_info.copy()

        input_text = (
            f"Меня зовут {original_info['first_name']} {original_info['last_name']}. "
            f"Свяжитесь со мной по почте {original_info['email']} или в телеграм: {original_info['telegram']}. "
            f"Мой телефон {original_info['phone']}. "
            "Мои проекты можно найти на GitHub: https://github.com/real_user/project1 и "
            "еще один тут http://github.com/another_user/project2."
        )
        expected_text = (
            f"Меня зовут {DUMMY_PERSONAL_INFO_MALE['first_name']} {DUMMY_PERSONAL_INFO_MALE['last_name']}. "
            f"Свяжитесь со мной по почте {DUMMY_PERSONAL_INFO_MALE['email']} или в телеграм: {DUMMY_PERSONAL_INFO_MALE['telegram']}. "
            f"Мой телефон {DUMMY_PERSONAL_INFO_MALE['phone']}. "
            f"Мои проекты можно найти на GitHub: {DUMMY_PERSONAL_INFO_MALE['github']}/project1 и "
            f"еще один тут {DUMMY_PERSONAL_INFO_MALE['github']}/project2."
        )

        result = scraper.anonymize_text(input_text)

        assert result == expected_text

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_replaces_pii_female(self, mock_manager):
        original_info = {
            "first_name": "Мария",
            "last_name": "Петрова",
            "phone": "+7(555)666-77-88",
            "email": "maria.petrova@email.com",
            "telegram": "https://t.me/real_maria",
            "sex": "Женский",
        }
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = original_info.copy()
        scraper.resume_info["personal_information"] = original_info.copy()

        input_text = (
            f"Меня зовут {original_info['first_name']} {original_info['last_name']}. "
            f"Мой телефон {original_info['phone']}."
        )

        result = scraper.anonymize_text(input_text)

        assert DUMMY_PERSONAL_INFO_FEMALE["first_name"] in result
        assert DUMMY_PERSONAL_INFO_FEMALE["last_name"] in result
        assert original_info["first_name"] not in result

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_respects_word_boundaries(self, mock_manager):
        original_info = {"first_name": "Иван", "sex": "Мужской"}
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = original_info.copy()
        scraper.resume_info["personal_information"] = original_info.copy()

        input_text = "Меня зовут Иван. Мой друг - Иванов. Это не я."
        expected_text = (
            f"Меня зовут {DUMMY_PERSONAL_INFO_MALE['first_name']}. Мой друг - Иванов. Это не я."
        )

        result = scraper.anonymize_text(input_text)

        assert result == expected_text

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_skips_fields_not_in_resume_info(self, mock_manager):
        original_info = {"first_name": "Иван", "last_name": "Иванов", "sex": "Мужской"}
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = original_info.copy()
        scraper.resume_info["personal_information"] = original_info.copy()

        phone_in_text = "+7(999)999-99-99"
        input_text = f"Меня зовут Иван Иванов. Мой телефон {phone_in_text}."
        expected_text = (
            f"Меня зовут {DUMMY_PERSONAL_INFO_MALE['first_name']} {DUMMY_PERSONAL_INFO_MALE['last_name']}. "
            f"Мой телефон {phone_in_text}."
        )

        result = scraper.anonymize_text(input_text)

        assert result == expected_text

    @patch("src.job_manager.resume_scraper.ANONYMIZE", False)
    def test_returns_input_unchanged_when_disabled(self, mock_manager):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        input_text = "Меня зовут Иван Иванов."

        result = scraper.anonymize_text(input_text)

        assert result == input_text


class TestDeanonymizePersonalInformation:
    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_restores_real_values_male(self, mock_manager):
        real_info = {
            "first_name": "Иван",
            "last_name": "Иванов",
            "sex": "Мужской",
            "email": "ivan.ivanov@email.com",
            "phone": "+7(111)222-33-44",
            "telegram": "https://t.me/real_ivan",
            "whatsapp": "https://wa.me/real_ivan",
            "linkedin": "https://linkedin.com/in/ivan-ivanov",
            "other_site": "https://www.ivan.ru",
        }
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = real_info.copy()

        dummy = DUMMY_PERSONAL_INFO_MALE
        input_text = (
            f"Меня зовут {dummy['first_name']} {dummy['last_name']}. "
            f"Почта: {dummy['email']}. Телефон: {dummy['phone']}. "
            f"Телеграм: {dummy['telegram']}."
        )

        result = scraper.deanonymize_personal_information(input_text)

        assert "Иван" in result
        assert "Иванов" in result
        assert "ivan.ivanov@email.com" in result
        assert "+7(111)222-33-44" in result
        assert "https://t.me/real_ivan" in result
        assert dummy["first_name"] not in result
        assert dummy["last_name"] not in result

    @patch("src.job_manager.resume_scraper.ANONYMIZE", False)
    def test_returns_output_unchanged_when_disabled(self, mock_manager):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = {"sex": "Мужской"}
        output = "Меня зовут Аристаний."

        result = scraper.deanonymize_personal_information(output)

        assert result == output


class TestAnonymizationRoundTrip:
    """Регрессионные тесты на краевые случаи анонимизации и деанонимизации."""

    @pytest.fixture
    def scraper(self, mock_manager):
        real_info = {
            "first_name": "Иван",
            "last_name": "Петров",
            "sex": "Мужской",
            "phone": "+7 (999) 111-22-33",
            "telegram": "@ivan_p",
            "linkedin": "https://linkedin.com/in/ivan/",
        }
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = real_info.copy()
        scraper.resume_info["personal_information"] = real_info.copy()
        return scraper

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_anonymizes_values_with_non_word_edges(self, scraper):
        text = "Пишите @ivan_p. LinkedIn: https://linkedin.com/in/ivan/ ."

        result = scraper.anonymize_text(text)

        assert "ivan" not in result
        assert "@aristaniy93" in result
        assert DUMMY_PERSONAL_INFO_MALE["linkedin"] in result

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    @pytest.mark.parametrize("phone", ["+79991112233", "8 999 111 22 33", "+7(999)111-22-33"])
    def test_anonymizes_phone_in_any_format(self, scraper, phone):
        result = scraper.anonymize_text(f"Телефон: {phone}.")

        assert result == f"Телефон: {DUMMY_PERSONAL_INFO_MALE['phone']}."

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_github_links_keep_punctuation_and_order(self, scraper):
        text = "Код: https://github.com/ivanp, https://github.com/myorg."

        anonymized = scraper.anonymize_text(text)
        dummy = DUMMY_PERSONAL_INFO_MALE["github"]
        assert anonymized == f"Код: {dummy}, {dummy}."

        output = f"Профили: {dummy} и {dummy}/repo, см. также https://github.com/pytorch"
        result = scraper.deanonymize_personal_information(output)

        assert result == (
            "Профили: https://github.com/ivanp и https://github.com/myorg/repo, "
            "см. также https://github.com/pytorch"
        )

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_deanonymizes_telegram_handle_without_garbage(self, scraper):
        result = scraper.deanonymize_personal_information(
            "Мой телеграм @aristaniy93"
        )

        assert result == "Мой телеграм @ivan_p"

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_deanonymizes_reformatted_dummy_phone(self, scraper):
        result = scraper.deanonymize_personal_information("Звоните: +79335753535")

        assert result == "Звоните: +7 (999) 111-22-33"

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_deanonymize_keeps_backslashes_in_real_value(self, scraper):
        scraper.personal_information["first_name"] = r"Ив\1ан"

        result = scraper.deanonymize_personal_information(DUMMY_PERSONAL_INFO_MALE["first_name"])

        assert result == r"Ив\1ан"


class TestNameDeclension:
    """ФИО заменяются во всех падежах в обе стороны."""

    def make_scraper(self, mock_manager, **real_info):
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = real_info.copy()
        scraper.resume_info["personal_information"] = real_info.copy()
        return scraper

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_round_trip_male(self, mock_manager):
        scraper = self.make_scraper(
            mock_manager,
            first_name="Павел",
            middle_name="Сергеевич",
            last_name="Петров",
            sex="Мужской",
        )
        text = "Павел Сергеевич Петров. Письмо от Павла Петрова, спасибо Павлу Сергеевичу Петрову."

        anonymized = scraper.anonymize_text(text)

        assert anonymized == (
            "Аристаний Астромерович Звяегольцев. Письмо от Аристания Звяегольцева, "
            "спасибо Аристанию Астромеровичу Звяегольцеву."
        )
        assert scraper.deanonymize_personal_information(anonymized) == text

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_round_trip_female(self, mock_manager):
        scraper = self.make_scraper(
            mock_manager,
            first_name="Мария",
            middle_name="Ивановна",
            last_name="Петрова",
            sex="Женский",
        )
        text = "Мария Ивановна Петрова. Работа с Марией Петровой, спасибо Марии Ивановне."

        anonymized = scraper.anonymize_text(text)

        assert anonymized == (
            "Аристания Астромеровна Звяегольцева. Работа с Аристанией Звяегольцевой, "
            "спасибо Аристании Астромеровне."
        )
        assert scraper.deanonymize_personal_information(anonymized) == text

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_anonymizes_yo_written_as_e(self, mock_manager):
        scraper = self.make_scraper(mock_manager, first_name="Пётр", sex="Мужской")

        result = scraper.anonymize_text("Пётр, Петр и Петра")

        assert result == "Аристаний, Аристаний и Аристания"

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_indeclinable_surname(self, mock_manager):
        scraper = self.make_scraper(mock_manager, last_name="Шевченко", sex="Мужской")

        assert scraper.anonymize_text("от Шевченко") == "от Звяегольцев"
        assert scraper.deanonymize_personal_information("от Звяегольцева") == "от Шевченко"

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_unrecognized_name_restored_in_original_form(self, mock_manager):
        scraper = self.make_scraper(mock_manager, first_name="John", sex="Мужской")

        assert scraper.anonymize_text("Меня зовут John") == "Меня зовут Аристаний"
        assert scraper.deanonymize_personal_information("Спасибо Аристанию") == "Спасибо John"


class TestTelegramReplacement:
    """Ник Telegram заменяется в любом формате записи, формат при этом сохраняется."""

    def make_scraper(self, mock_manager, telegram):
        real_info = {"sex": "Мужской", "telegram": telegram}
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = real_info.copy()
        scraper.resume_info["personal_information"] = real_info.copy()
        return scraper

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    @pytest.mark.parametrize("telegram", ["https://t.me/ivan_p", "t.me/ivan_p", "@ivan_p", "ivan_p"])
    def test_anonymizes_username_in_any_format(self, mock_manager, telegram):
        scraper = self.make_scraper(mock_manager, telegram)

        result = scraper.anonymize_text("Пишите https://t.me/ivan_p, t.me/Ivan_P или @ivan_p")

        assert result == "Пишите https://t.me/aristaniy93, t.me/aristaniy93 или @aristaniy93"

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_deanonymizes_username_keeping_format(self, mock_manager):
        scraper = self.make_scraper(mock_manager, "https://t.me/ivan_p")

        result = scraper.deanonymize_personal_information(
            "тг: https://t.me/aristaniy93 | t.me/aristaniy93 | @aristaniy93"
        )

        assert result == "тг: https://t.me/ivan_p | t.me/ivan_p | @ivan_p"

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_hallucinated_contacts_do_not_get_telegram(self, mock_manager):
        scraper = self.make_scraper(mock_manager, "@ivan_p")
        output = (
            f"{DUMMY_PERSONAL_INFO_MALE['whatsapp']} | {DUMMY_PERSONAL_INFO_MALE['email']} | "
            f"{DUMMY_PERSONAL_INFO_MALE['github']}"
        )

        assert scraper.deanonymize_personal_information(output) == output

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_phone_instead_of_username_replaced_as_whole(self, mock_manager):
        scraper = self.make_scraper(mock_manager, "+7 999 111-22-33")

        assert scraper.anonymize_text("тг: +7 999 111-22-33") == (
            f"тг: {DUMMY_PERSONAL_INFO_MALE['telegram']}"
        )
        assert scraper.deanonymize_personal_information(
            "тг: @aristaniy93 или t.me/aristaniy93"
        ) == ("тг: +7 999 111-22-33 или +7 999 111-22-33")


class TestFixDummyNames:
    """Искажённые LLM ФИО-пустышки исправляются перед деанонимизацией."""

    def make_scraper(self, mock_manager, sex="Мужской", **names):
        real_info = {"sex": sex, **names}
        scraper = ResumeScraper(mock_manager, "Python Developer", "abc123", MagicMock())
        scraper.personal_information = real_info.copy()
        scraper.resume_info["personal_information"] = real_info.copy()
        return scraper

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    @pytest.mark.parametrize(
        "output, expected",
        [
            ("С уважением, Ари\u0301станий Звягольцев", "С уважением, Павел Петров"),
            ("Спасибо Ариста\u0301нию Звягольцеву", "Спасибо Павлу Петрову"),
            ("Письмо от Аристаниия Звяегольцова", "Письмо от Павла Петрова"),
        ],
    )
    def test_fixes_stress_marks_and_typos_male(self, mock_manager, output, expected):
        scraper = self.make_scraper(mock_manager, first_name="Павел", last_name="Петров")

        assert scraper.deanonymize_personal_information(output) == expected

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_fixes_typos_female(self, mock_manager):
        scraper = self.make_scraper(
            mock_manager, sex="Женский", first_name="Мария", last_name="Петрова"
        )

        result = scraper.deanonymize_personal_information("С уважением, Ари\u0301стания Звягольцева")

        assert result == "С уважением, Мария Петрова"

    @patch("src.job_manager.resume_scraper.ANONYMIZE", True)
    def test_keeps_similar_but_different_words(self, mock_manager):
        scraper = self.make_scraper(mock_manager, first_name="Павел", last_name="Петров")
        output = "Аристотель и Астрономия, Аристарх, Звягинцев, а также ёлка и йогурт"

        assert scraper.deanonymize_personal_information(output) == output

