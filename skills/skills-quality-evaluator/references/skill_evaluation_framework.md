# Skill Evaluation Framework

Use this framework to evaluate skills contributed to a skills marketplace or enterprise skill catalog.

## Objectives

Published skills should be:

- agent-ready
- deterministic and reliable
- safe within enterprise environments
- valuable and reusable across teams

## Dimensions and Weights

| Dimension | Weight | Core Question |
| --- | ---: | --- |
| Trigger & Discoverability | 15 | Can the agent correctly trigger this skill? |
| Instruction Quality | 20 | Are the instructions clear, structured, and executable? |
| Determinism & Reliability | 20 | Is the output stable and repeatable? |
| Structure & Best Practice | 15 | Does it follow skill engineering standards? |
| Safety & Compliance | 15 | Is it safe for enterprise use? |
| Business Value & Reusability | 15 | Does it provide real, reusable value? |

## Dimension Guidance

### 1. Trigger & Discoverability

Look for:

- explicit positive triggers
- explicit negative triggers
- limited ambiguity
- description text that helps the agent trigger the skill correctly

### 2. Instruction Quality

Look for:

- executable steps
- clear sequencing
- direct action verbs
- low ambiguity

### 3. Determinism & Reliability

Look for:

- deterministic scripts where needed
- clear input and output expectations
- bounded behavior
- low dependence on vague free-form reasoning

### 4. Structure & Best Practice

Look for:

- correct frontmatter
- clean `SKILL.md`
- reasonable use of `scripts/`, `references/`, and `assets/`
- progressive disclosure instead of one overloaded instruction file

### 5. Safety & Compliance

Look for:

- use boundaries
- restricted or prohibited cases where appropriate
- safe data handling
- prevention of unsafe or misleading flows

### 6. Business Value & Reusability

Look for:

- clear business need
- reuse across teams or repeated workflows
- non-trivial value beyond what the base model already does
- low redundancy with existing skills

## Score Interpretation

| Total Score | Level | Action |
| ---: | --- | --- |
| 90-100 | Excellent | Auto-approved for publishing |
| 80-89 | Good | Minor improvements required |
| 70-79 | Needs Improvement | Revision required before approval |
| Below 70 | Rejected | Not approved for publishing |
