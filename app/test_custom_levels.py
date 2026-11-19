"""직접 입력한 기술 수준의 화면 반영과 계정 전환을 확인한다."""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app import levels
from backend import chat_profiles
from backend.levels import job_subskills


def sidebar():
    import streamlit as st

    from app import levels

    with st.sidebar:
        mine = levels.sidebar_summary(st.session_state.get("test_session"), "cloud_engineer")
    levels.survey_button(st.session_state.get("test_session"), "cloud_engineer")  # 화면에서는 학습 상담에 있다
    st.text(str(levels.chat_skills("cloud_engineer", mine)))


def save_survey():
    import streamlit as st

    from app import levels

    if st.button("저장"):
        levels.save_levels(st.session_state["test_session"], {"C1-4": 4, "A1-1": 3})


class CustomLevelTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"BEDROCK_MOCK": "1"}))

    def test_guest_can_enter_and_update_one_named_level(self):
        at = AppTest.from_function(sidebar).run()
        self.assertIn("custom_level_name", [widget.key for widget in at.text_input])
        at.text_input(key="custom_level_name").set_value("  Compiler Design ")
        at.selectbox(key="custom_level_value").set_value(4)
        at.button(key="save_custom_level").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["levels"], {"custom:Compiler Design": 4})
        self.assertIn("Compiler Design", at.main.text[-1].value)
        at.text_input(key="custom_level_name").set_value("compiler design")
        at.selectbox(key="custom_level_value").set_value(0)
        at.button(key="save_custom_level").click().run()
        self.assertEqual(at.session_state["levels"], {"custom:Compiler Design": 0})
        self.assertNotIn("Compiler Design", at.main.text[-1].value)
        self.assertTrue(any("Compiler Design" in t.value for t in at.sidebar.text))

    def test_chat_lists_one_canonical_name_and_drops_zero_even_with_a_survey_level(self):
        at = AppTest.from_function(sidebar).run()
        at.session_state["levels"] = {"custom:도커": 5, "C1-4": 4}
        at.session_state["levels_example"] = False
        at.run()
        self.assertIn("'Docker'", at.main.text[-1].value)
        self.assertNotIn("'도커'", at.main.text[-1].value)
        at.session_state["levels"] = {"custom:도커": 0, "C1-4": 4}
        at.run()
        self.assertNotIn("'Docker'", at.main.text[-1].value)
        self.assertNotIn("'도커'", at.main.text[-1].value)

    def test_chat_merges_a_market_alias_with_its_direct_rating(self):
        at = AppTest.from_function(sidebar).run()
        at.session_state["levels"] = {"custom:Docker": 4}
        at.session_state["levels_example"] = False
        demand = {"total_postings": 1, "skills": [{"skill": "도커", "postings": 1, "required": 1, "preferred": 0}]}
        with patch("backend.api.get_demand", return_value=demand):
            at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.main.text[-1].value, "['Docker']")

    def test_chat_keeps_only_qualifying_direct_ratings_when_demand_is_unavailable(self):
        at = AppTest.from_function(sidebar).run()
        at.session_state["levels"] = {"custom:Compiler Design": 3, "custom:OpenTofu": 2, "C1-4": 5}
        at.session_state["levels_example"] = False
        with patch("backend.api.get_demand", side_effect=LookupError("no market snapshot")):
            at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.main.text[-1].value, "['Compiler Design']")

    def test_account_change_restores_only_its_levels_and_clears_drafts(self):
        session = SimpleNamespace(nickname="test-owner")
        with patch.object(chat_profiles, "load_profile", return_value={"levels": {"custom:C++": 5}}):
            at = AppTest.from_function(sidebar).run()
            at.session_state["levels"] = {"custom:Guest": 4}
            at.session_state["levels_example"] = False
            at.session_state["lv_C1-4"] = 2
            at.session_state["test_session"] = session
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state["levels"], {"custom:C++": 5})
            self.assertNotIn("lv_C1-4", at.session_state)
            at.text_input(key="custom_level_name").set_value("owner draft").run()
            at.session_state["test_session"] = None
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state["levels"], levels.EXAMPLE_LEVELS)
            self.assertTrue(at.session_state["levels_example"])
            self.assertEqual(at.text_input(key="custom_level_name").value, "")

    def test_guest_levels_wait_for_an_explicit_save_to_an_empty_account(self):
        database = Path(self.enterContext(TemporaryDirectory())) / "profiles.sqlite3"
        self.enterContext(patch.dict(os.environ, {"SKILLPATH_MOCK_PROFILE_DB": str(database)}))
        session = chat_profiles.create_profile("level-test", "integration test password")
        at = AppTest.from_function(sidebar).run()
        at.session_state["levels"] = {"custom:C++": 4, "A1-1": 2}
        at.session_state["levels_example"] = False
        at.session_state["test_session"] = session
        at.run()
        self.assertEqual(at.session_state["levels"], {"custom:C++": 4, "A1-1": 2})
        self.assertEqual(chat_profiles.load_profile(session)["levels"], {})
        at.text_input(key="custom_level_name").set_value("SQL")
        at.selectbox(key="custom_level_value").set_value(3)
        at.button(key="save_custom_level").click().run()
        self.assertEqual(chat_profiles.load_profile(session)["levels"], {"custom:SQL": 3})
        at.button(key="save_guest_levels").click().run()
        expected = {"custom:SQL": 3, "custom:C++": 4, "A1-1": 2}
        self.assertEqual(chat_profiles.load_profile(session)["levels"], expected)
        reopened = AppTest.from_function(sidebar)
        reopened.session_state["test_session"] = chat_profiles.sign_in("level-test", "integration test password")
        reopened.run()
        self.assertEqual(reopened.session_state["levels"], expected)
        self.assertNotIn("save_guest_levels", [button.key for button in reopened.button])

    def test_failed_profile_read_does_not_import_guest_levels(self):
        with patch.object(chat_profiles, "load_profile", side_effect=RuntimeError):
            at = AppTest.from_function(sidebar).run()
            at.session_state["levels"] = {"custom:Guest": 4}
            at.session_state["levels_example"] = False
            at.session_state["test_session"] = SimpleNamespace(nickname="test-owner")
            at.run()
            self.assertEqual(at.session_state["levels"], levels.EXAMPLE_LEVELS)
            self.assertNotIn("save_guest_levels", [button.key for button in at.button])

    def test_retry_keeps_the_latest_edit_to_a_guest_level(self):
        session = SimpleNamespace(nickname="test-owner")
        with (patch.object(chat_profiles, "load_profile", return_value={"levels": {}}),
              patch.object(chat_profiles, "update_profile", side_effect=RuntimeError) as store):
            at = AppTest.from_function(sidebar).run()
            at.session_state["levels"] = {"custom:C++": 4}
            at.session_state["levels_example"] = False
            at.session_state["test_session"] = session
            at.run()
            at.text_input(key="custom_level_name").set_value("C++")
            at.selectbox(key="custom_level_value").set_value(0)
            at.button(key="save_custom_level").click().run()
            self.assertEqual(at.session_state["levels"], {"custom:C++": 0})
            store.side_effect = lambda _session, *, levels: {"levels": dict(levels)}
            at.button(key="save_guest_levels").click().run()
            self.assertEqual(at.session_state["levels"], {"custom:C++": 0})
            self.assertNotIn("save_guest_levels", [button.key for button in at.button])

    def test_save_only_changed_survey_answers_and_reuse_merged_profile(self):
        session = SimpleNamespace(nickname="test-owner")
        concurrent = {"C1-4": 4, "A1-1": 2, "custom:C++": 5}
        with patch.object(chat_profiles, "update_profile", return_value={"levels": concurrent}) as store:
            at = AppTest.from_function(save_survey).run()
            at.session_state["test_session"] = session
            at.session_state["levels"] = {"C1-4": 2, "A1-1": 3, "custom:C++": 5}
            at.session_state["levels_example"] = False
            at.button[0].click().run()
            store.assert_called_once_with(session, levels={"C1-4": 4})
            self.assertEqual(at.session_state["levels"], concurrent)

    def test_survey_draft_does_not_overwrite_an_unedited_answer_after_refresh(self):
        at = AppTest.from_function(sidebar).run()
        mine = dict.fromkeys(job_subskills("cloud_engineer"), 0)
        at.session_state["levels"] = {**mine, "C1-4": 1}
        at.session_state["levels_example"] = False
        at.button(key="open_levels").click().run()
        at.session_state["levels"] = {**mine, "C1-4": 2}
        at.session_state["lv_C8-3"] = 4
        at.session_state["lv_page"] = 7
        at.button(key="open_levels").click().run()
        at.button(key="open_levels").click()
        at.button(key="save_levels").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["levels"]["C1-4"], 2)
        self.assertEqual(at.session_state["levels"]["C8-3"], 4)


if __name__ == "__main__":
    unittest.main()
