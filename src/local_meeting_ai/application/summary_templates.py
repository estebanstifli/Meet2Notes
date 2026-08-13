from __future__ import annotations

# ruff: noqa: E501 - Built-in prompt copy is intentionally kept readable as prose.
from typing import Any


def _section(
    title: str,
    instruction: str,
    format: str = "list",
    item_format: str | None = None,
) -> dict[str, str | None]:
    return {
        "title": title,
        "instruction": instruction,
        "format": format,
        "item_format": item_format,
    }


BUILTIN_SUMMARY_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "name": "General Meeting",
        "description": "A balanced summary with highlights, decisions and follow-up work.",
        "system_prompt": "Create accurate, practical meeting notes without inventing facts.",
        "user_prompt_template": "Turn the transcript into clear notes for the meeting participants.",
        "sections": [
            _section("Summary", "Summarize the purpose and main topics.", "paragraph"),
            _section("Highlights", "List the most important points discussed."),
            _section("Decisions", "List only decisions explicitly made."),
            _section(
                "Action Items",
                "List explicit tasks, owners and deadlines. Do not infer missing values.",
                "list",
                "| Task | Owner | Deadline |",
            ),
            _section("Open Questions", "List unresolved questions and pending topics."),
        ],
    },
    {
        "name": "Daily Stand-up",
        "description": "Yesterday's progress, today's plan and blockers by participant.",
        "system_prompt": "Produce concise stand-up notes grouped by participant when possible.",
        "user_prompt_template": "Extract delivery progress and impediments from the stand-up.",
        "sections": [
            _section("Completed", "List work completed since the previous stand-up."),
            _section("Today's Plan", "List the work each participant plans to do next."),
            _section("Blockers", "List blockers, dependencies and requested help."),
            _section("Follow-ups", "List explicit follow-up actions and owners."),
        ],
    },
    {
        "name": "Project Sync",
        "description": "Project status, milestones, risks, dependencies and decisions.",
        "system_prompt": "Act as a precise project coordinator and preserve explicit ownership.",
        "user_prompt_template": "Convert the transcript into a project status update.",
        "sections": [
            _section("Status", "Summarize current project status.", "paragraph"),
            _section("Milestones", "List milestone progress and dates mentioned."),
            _section("Risks and Dependencies", "List risks, blockers and dependencies."),
            _section("Decisions", "List explicit project decisions."),
            _section("Next Steps", "List next steps, owners and deadlines.", "list", "| Next step | Owner | Due |"),
        ],
    },
    {
        "name": "Sales Call",
        "description": "Customer needs, objections, commitments and next commercial steps.",
        "system_prompt": "Capture customer language accurately and never fabricate commercial intent.",
        "user_prompt_template": "Create useful CRM-ready notes from this sales conversation.",
        "sections": [
            _section("Customer Context", "Summarize the customer situation and goals.", "paragraph"),
            _section("Needs", "List stated needs, pain points and success criteria."),
            _section("Objections", "List concerns, objections and unanswered questions."),
            _section("Agreements", "List explicit agreements and commitments."),
            _section("Next Steps", "List next steps, owners and dates.", "list", "| Next step | Owner | Date |"),
        ],
    },
    {
        "name": "Technical Meeting",
        "description": "Architecture, implementation decisions, risks and engineering tasks.",
        "system_prompt": "Preserve technical terminology, constraints and uncertainty accurately.",
        "user_prompt_template": "Create engineering notes from the technical discussion.",
        "sections": [
            _section("Technical Summary", "Summarize systems and topics discussed.", "paragraph"),
            _section("Technical Decisions", "List architecture and implementation decisions."),
            _section("Alternatives Considered", "List alternatives and stated trade-offs."),
            _section("Risks and Unknowns", "List technical risks and unresolved questions."),
            _section("Engineering Actions", "List tasks, owners and deadlines.", "list", "| Task | Owner | Deadline |"),
        ],
    },
    {
        "name": "Interview",
        "description": "Topics, evidence, answers and follow-up questions from an interview.",
        "system_prompt": "Represent the interviewee faithfully and separate facts from opinions.",
        "user_prompt_template": "Turn the interview transcript into structured research notes.",
        "sections": [
            _section("Overview", "Summarize the interview scope and participant context.", "paragraph"),
            _section("Key Insights", "List the strongest insights and supporting evidence."),
            _section("Notable Answers", "Capture important answers without changing their meaning."),
            _section("Follow-up Questions", "List questions worth investigating next."),
        ],
    },
    {
        "name": "Lecture Notes",
        "description": "Structured study notes with concepts, examples and review points.",
        "system_prompt": "Create accurate educational notes and do not add knowledge absent from the transcript.",
        "user_prompt_template": "Organize the lecture into clear revision notes.",
        "sections": [
            _section("Overview", "Summarize the topic and learning objectives.", "paragraph"),
            _section("Core Concepts", "Explain the concepts introduced in the lecture."),
            _section("Examples", "List examples and demonstrations from the transcript."),
            _section("Key Terms", "List important terms with transcript-grounded definitions."),
            _section("Review Questions", "Create review questions answerable from the transcript."),
        ],
    },
    {
        "name": "Brainstorming",
        "description": "Ideas, themes, evaluation criteria and promising directions.",
        "system_prompt": "Preserve unconventional ideas and clearly distinguish evaluation from decisions.",
        "user_prompt_template": "Organize the brainstorming session without discarding minority ideas.",
        "sections": [
            _section("Challenge", "Summarize the problem or opportunity.", "paragraph"),
            _section("Ideas", "List all materially distinct ideas raised."),
            _section("Themes", "Group related ideas into themes."),
            _section("Promising Directions", "List ideas participants explicitly favored and why."),
            _section("Experiments", "List proposed tests or next steps."),
        ],
    },
    {
        "name": "Formal Minutes",
        "description": "Formal minutes with attendance, agenda, motions and recorded actions.",
        "system_prompt": "Write formal minutes using only information explicitly present in the transcript.",
        "user_prompt_template": "Prepare a concise formal record of the meeting.",
        "sections": [
            _section("Meeting Details", "Record date, purpose and location only when stated.", "text"),
            _section("Attendance", "List named attendees and absences when stated."),
            _section("Agenda and Discussion", "Summarize each agenda topic."),
            _section("Motions and Decisions", "Record motions, votes and decisions exactly when available."),
            _section("Actions", "List approved actions, owners and dates.", "list", "| Action | Owner | Due |"),
            _section("Adjournment", "Record closing time and next meeting details when stated.", "text"),
        ],
    },
)


def render_summary_template(template: dict[str, Any]) -> str:
    lines = [
        str(template.get("user_prompt_template") or "Create structured notes."),
        "Use exactly the following Markdown sections, in this order:",
    ]
    for section in template.get("sections", []):
        lines.extend(
            [
                f"## {section['title']}",
                f"Instruction: {section['instruction']}",
                f"Format: {section['format']}",
            ]
        )
        if section.get("item_format"):
            lines.append(f"Item format: {section['item_format']}")
    lines.append(
        "If a section has no supporting information, write 'Not specified'. "
        "Never infer owners, dates, decisions or facts."
    )
    return "\n".join(lines)
