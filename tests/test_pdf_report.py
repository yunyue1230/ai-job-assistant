import unittest

from backend.app.infrastructure.pdf_report import build_analysis_report


class PdfReportTests(unittest.TestCase):
    def test_builds_pdf_with_structured_report_content(self) -> None:
        pdf = build_analysis_report(
            "resume.pdf",
            {
                "match_score": 85,
                "matched_skills": ["Python", "LLM 应用"],
                "missing_skills": ["Kubernetes"],
                "strengths": ["Built AI applications"],
                "gaps": [],
                "resume_improvement_tips": ["Add measurable impact"],
                "interview_questions": ["How do you evaluate an LLM?"],
            },
        )

        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)


if __name__ == "__main__":
    unittest.main()
