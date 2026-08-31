document.body.addEventListener("htmx:configRequest", (event) => {
  const token = document.querySelector("[name=csrfmiddlewaretoken]")?.value;
  if (token) event.detail.headers["X-CSRFToken"] = token;
});

window.sc06BriefingForm = (config) => ({
  config,
  answers: { ...(config.initialAnswers || config.answers || {}) },

  init() {
    this.$nextTick(() => this.syncFromDom(this.$root));
  },

  syncFromDom(root) {
    const questionTypes = this.questionTypes;
    root.querySelectorAll("[data-sc06-answer]").forEach((field) => {
      const questionId = field.dataset.sc06Answer;
      if (!questionId) return;
      const rawValue = field.value;
      if (rawValue === "") {
        this.answers[questionId] = null;
      } else if (questionTypes[questionId] === "boolean") {
        this.answers[questionId] = rawValue === "true";
      } else {
        this.answers[questionId] = rawValue;
      }
    });
  },

  get questionTypes() {
    const types = {};
    for (const section of this.config.sections || []) {
      for (const question of section.questions || []) types[question.id] = question.type;
    }
    return types;
  },

  get visibility() {
    const effectiveAnswers = {};
    const sections = {};
    const questions = {};
    for (const section of this.config.sections || []) {
      const sectionCondition = section.condition ?? section.visible_when ?? null;
      const sectionVisible = this.evaluate(sectionCondition, effectiveAnswers);
      sections[section.id] = sectionVisible;
      for (const question of section.questions || []) {
        const questionCondition = question.condition ?? question.visible_when ?? null;
        const questionVisible =
          sectionVisible && this.evaluate(questionCondition, effectiveAnswers);
        questions[question.id] = questionVisible;
        if (questionVisible && !this.isEmpty(this.answers[question.id])) {
          effectiveAnswers[question.id] = this.answers[question.id];
        }
      }
    }
    return { sections, questions };
  },

  isSectionVisible(sectionId) {
    return Boolean(this.visibility.sections[sectionId]);
  },

  isQuestionVisible(questionId, sectionId) {
    return (
      Boolean(this.visibility.sections[sectionId]) &&
      Boolean(this.visibility.questions[questionId])
    );
  },

  setContainerEnabled(container, enabled) {
    container.querySelectorAll("input, select, textarea, button").forEach((control) => {
      control.disabled = !enabled;
    });
    container.setAttribute("aria-hidden", enabled ? "false" : "true");
  },

  evaluate(condition, answers) {
    if (!condition) return true;
    const operator = condition.operator;
    if (operator === "all") {
      return (condition.conditions || []).every((child) => this.evaluate(child, answers));
    }
    if (operator === "any") {
      return (condition.conditions || []).some((child) => this.evaluate(child, answers));
    }
    const actual = answers[condition.field];
    if (operator === "equals") return actual === condition.value;
    if (operator === "not_equals") return actual !== condition.value;
    if (operator === "in") return (condition.value || []).includes(actual);
    return false;
  },

  isEmpty(value) {
    return value === null || value === undefined || value === "";
  },

  get visibleQuestionIds() {
    return Object.entries(this.visibility.questions)
      .filter(([, visible]) => visible)
      .map(([questionId]) => questionId);
  },

  get visibleQuestionCount() {
    return this.visibleQuestionIds.length;
  },

  get answeredCount() {
    return this.visibleQuestionIds.filter((questionId) => !this.isEmpty(this.answers[questionId]))
      .length;
  },

  get progressPercent() {
    if (!this.visibleQuestionCount) return 0;
    return Math.round((this.answeredCount / this.visibleQuestionCount) * 100);
  },
});
