document.body.addEventListener("htmx:configRequest", (event) => {
  const token = document.querySelector("[name=csrfmiddlewaretoken]")?.value;
  if (token) event.detail.headers["X-CSRFToken"] = token;
});

const setSc04PollingError = (event, visible) => {
  const source = event.detail?.elt;
  const pollingRegion = source?.closest?.("[data-sc04-poll]") || source;
  const message = pollingRegion?.querySelector?.("[data-poll-error]");
  if (message) message.hidden = !visible;
};

const getOrCreateToastContainer = () => {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("aside");
    container.id = "toast-container";
    container.className =
      "fixed top-20 right-4 left-4 sm:left-auto sm:right-6 z-50 flex flex-col gap-2.5 pointer-events-none max-w-sm w-auto sm:w-full";
    container.setAttribute("aria-label", "Notificações do sistema");
    container.setAttribute("aria-live", "polite");
    document.body.appendChild(container);
  }
  return container;
};

window.showToast = ({ title = "", message = "", type = "warning", duration = 5000 } = {}) => {
  const container = getOrCreateToastContainer();
  const toast = document.createElement("div");
  toast.className = `pointer-events-auto toast-notification toast-${type}`;
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  toast.dataset.toastDynamic = "true";
  if (type === "warning") {
    toast.dataset.toastCategory = "network-timeout";
  }

  const defaultTitle =
    type === "warning" ? "Atenção" : type === "error" ? "Erro" : type === "success" ? "Sucesso" : "Notificação";

  const iconSvg =
    type === "warning"
      ? '<svg class="h-5 w-5 text-ambar" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>'
      : type === "error"
      ? '<svg class="h-5 w-5 text-carmim" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>'
      : type === "success"
      ? '<svg class="h-5 w-5 text-turquesa" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg>'
      : '<svg class="h-5 w-5 text-turquesa" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>';

  toast.innerHTML = `
    <span class="toast-icon shrink-0 mt-0.5" aria-hidden="true">${iconSvg}</span>
    <div class="toast-content min-w-0 flex-1">
      <p class="toast-title">${title || defaultTitle}</p>
      <p class="toast-text">${message}</p>
    </div>
    <button type="button" class="toast-dismiss" aria-label="Dispensar notificação">
      <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    </button>
  `;

  let timer = null;
  const dismiss = () => {
    if (timer) clearTimeout(timer);
    toast.style.transition = "opacity 0.2s ease, transform 0.2s ease";
    toast.style.opacity = "0";
    toast.style.transform = "scale(0.95)";
    setTimeout(() => {
      toast.remove();
      if (container.children.length === 0) {
        container.remove();
      }
    }, 200);
  };

  const startTimer = () => {
    if (duration > 0) {
      timer = setTimeout(dismiss, duration);
    }
  };

  toast.querySelector(".toast-dismiss")?.addEventListener("click", dismiss);
  toast.addEventListener("mouseenter", () => {
    if (timer) clearTimeout(timer);
  });
  toast.addEventListener("mouseleave", startTimer);

  container.appendChild(toast);
  startTimer();
  return toast;
};

document.body.addEventListener("htmx:responseError", (event) => {
  setSc04PollingError(event, true);
});
document.body.addEventListener("htmx:sendError", (event) => {
  setSc04PollingError(event, true);
});
document.body.addEventListener("htmx:timeout", (event) => {
  setSc04PollingError(event, true);
  window.showToast({
    title: "Instabilidade de rede",
    message: "O servidor demorou para responder. Tentando restabelecer sincronização...",
    type: "warning",
    duration: 5000,
  });
});
document.body.addEventListener("htmx:afterSwap", (event) => {
  setSc04PollingError(event, false);
  document.querySelectorAll('[data-toast-category="network-timeout"]').forEach((el) => {
    el.remove();
  });
  const container = document.getElementById("toast-container");
  if (container && container.children.length === 0) {
    container.remove();
  }
  const root = event.detail?.target || document;
  if (root.querySelectorAll) {
    root.querySelectorAll('[data-mask="document"], [data-sc06-answer="current_cnpj"], input[name="client_document"]').forEach((field) => {
      if (field.value) field.value = formatDocument(field.value);
    });
    root.querySelectorAll('[data-mask="phone"], input[name="contact_phone"]').forEach((field) => {
      const countrySelect = field.form?.querySelector('select[name="phone_country"]') || document.getElementById("id_phone_country");
      const country = countrySelect ? countrySelect.value : "55";
      if (field.value) field.value = formatPhone(field.value, country);
    });
  }
});

const formatDocument = (value) => {
  if (!value) return "";
  const digits = String(value).replace(/\D/g, "").slice(0, 14);
  if (digits.length <= 11) {
    if (digits.length <= 3) return digits;
    if (digits.length <= 6) return `${digits.slice(0, 3)}.${digits.slice(3)}`;
    if (digits.length <= 9) return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6)}`;
    return `${digits.slice(0, 3)}.${digits.slice(3, 6)}.${digits.slice(6, 9)}-${digits.slice(9)}`;
  }
  if (digits.length <= 12) {
    return `${digits.slice(0, 2)}.${digits.slice(2, 5)}.${digits.slice(5, 8)}/${digits.slice(8)}`;
  }
  return `${digits.slice(0, 2)}.${digits.slice(2, 5)}.${digits.slice(5, 8)}/${digits.slice(8, 12)}-${digits.slice(12)}`;
};

const formatPhone = (value, country = "55") => {
  if (!value) return "";
  const raw = String(value);
  const hasPlus = raw.startsWith("+");
  const digits = raw.replace(/\D/g, "");

  if (!digits) return hasPlus ? "+" : "";

  if (country === "55") {
    const cleanDigits = (digits.startsWith("55") && digits.length > 11) ? digits.slice(2) : digits;
    const localDigits = cleanDigits.slice(0, 11);
    if (localDigits.length <= 2) {
      return localDigits.length === 2 ? `(${localDigits}) ` : `(${localDigits}`;
    }
    const ddd = localDigits.slice(0, 2);
    const rest = localDigits.slice(2);
    if (rest.length <= 4) {
      return `(${ddd}) ${rest}`;
    }
    if (rest.length <= 8) {
      return `(${ddd}) ${rest.slice(0, 4)}-${rest.slice(4)}`;
    }
    return `(${ddd}) ${rest.slice(0, 5)}-${rest.slice(5, 9)}`;
  }

  let cleanDigits = digits;
  if (cleanDigits.startsWith(country) && cleanDigits.length > country.length + 5) {
    cleanDigits = cleanDigits.slice(country.length);
  }
  return cleanDigits.slice(0, 14);
};

window.formatPhone = formatPhone;

document.addEventListener("input", (e) => {
  const target = e.target;
  if (!target || !target.matches) return;
  if (
    target.matches('[data-mask="document"]') ||
    target.matches('[data-sc06-answer="current_cnpj"]') ||
    target.name === "client_document"
  ) {
    const start = target.selectionStart;
    const oldLength = target.value.length;
    const formatted = formatDocument(target.value);
    if (target.value !== formatted) {
      target.value = formatted;
      if (start !== null) {
        const newLength = formatted.length;
        const newPos = Math.max(0, start + (newLength - oldLength));
        target.setSelectionRange(newPos, newPos);
      }
    }
  }

  if (
    target.matches('[data-mask="phone"]') ||
    target.name === "contact_phone"
  ) {
    const start = target.selectionStart;
    const oldLength = target.value.length;
    const countrySelect = target.form?.querySelector('select[name="phone_country"]') || document.getElementById("id_phone_country");
    const country = countrySelect ? countrySelect.value : "55";
    const formatted = formatPhone(target.value, country);
    if (target.value !== formatted) {
      target.value = formatted;
      if (start !== null) {
        const newLength = formatted.length;
        const newPos = Math.max(0, start + (newLength - oldLength));
        target.setSelectionRange(newPos, newPos);
      }
    }
  }

  clearFieldValidationError(target);
});

function clearFieldValidationError(target) {
  if (!target || !target.matches || target.getAttribute("aria-invalid") !== "true") return;

  let isValid = false;
  if (target.type === "checkbox" || target.type === "radio") {
    isValid = target.checked;
  } else if (target.type === "file") {
    isValid = Boolean(target.files && target.files.length > 0);
  } else if (target.tagName === "SELECT") {
    isValid = target.value !== "";
  } else {
    isValid = Boolean(target.value && target.value.trim().length > 0);
  }

  if (isValid) {
    target.removeAttribute("aria-invalid");
    const describedBy = target.getAttribute("aria-describedby");
    if (describedBy) {
      describedBy.split(/\s+/).forEach((id) => {
        const errEl = document.getElementById(id);
        if (
          errEl &&
          (errEl.classList.contains("field-error") ||
            errEl.classList.contains("sc06-dark-field-error") ||
            errEl.classList.contains("sc05-dark-field-error") ||
            errEl.getAttribute("role") === "alert")
        ) {
          errEl.style.display = "none";
        }
      });
    }
    const fieldContainer =
      target.closest(".sc04-file-control")?.parentElement ||
      target.closest(".sc04-confirmation")?.parentElement ||
      target.closest(".sc06-dark-field")?.parentElement ||
      target.closest(".sc06-field-control")?.parentElement ||
      target.closest(".sc05-dark-field")?.parentElement ||
      target.closest("div");
    if (fieldContainer) {
      const siblingErrors = fieldContainer.querySelectorAll(
        ".field-error, .sc06-dark-field-error, .sc05-dark-field-error, [role='alert']"
      );
      siblingErrors.forEach((el) => {
        if (
          !el.classList.contains("form-error") &&
          !el.classList.contains("sc06-dark-form-error") &&
          !el.classList.contains("sc05-dark-error")
        ) {
          el.style.display = "none";
        }
      });
    }
  }
}

document.addEventListener("change", (e) => {
  if (e.target) {
    clearFieldValidationError(e.target);
  }
});


document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('[data-mask="document"], [data-sc06-answer="current_cnpj"], input[name="client_document"]').forEach((field) => {
    if (field.value) field.value = formatDocument(field.value);
  });
  document.querySelectorAll('[data-mask="phone"], input[name="contact_phone"]').forEach((field) => {
    const countrySelect = field.form?.querySelector('select[name="phone_country"]') || document.getElementById("id_phone_country");
    const country = countrySelect ? countrySelect.value : "55";
    if (field.value) field.value = formatPhone(field.value, country);
  });
});

window.sc06BriefingForm = (config) => ({
  config,
  answers: { ...(config.initialAnswers || config.answers || {}) },
  initialSnapshot: "",
  isSubmitting: false,
  showUnsavedModal: false,
  showCancelModal: false,
  pendingNavigationUrl: "",

  init() {
    this.$nextTick(() => {
      this.syncFromDom(this.$root);
      this.$root.querySelectorAll('[data-mask="document"], [data-sc06-answer="current_cnpj"]').forEach((field) => {
        if (field.value) field.value = formatDocument(field.value);
      });
      this.$root.querySelectorAll('[data-mask="phone"], input[name="contact_phone"]').forEach((field) => {
        if (field.value) field.value = formatPhone(field.value);
      });
      this.initialSnapshot = JSON.stringify(this.answers);
    });

    window.addEventListener("beforeunload", (e) => {
      if (this.isDirty && !this.isSubmitting) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
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

  get visibleSections() {
    return (this.config.sections || [])
      .filter((section) => this.isSectionVisible(section.id))
      .map((section) => section.id);
  },

  getSectionIndex(sectionId) {
    const index = this.visibleSections.indexOf(sectionId);
    if (index === -1) return "—";
    return String(index + 1).padStart(2, "0");
  },

  get parsedPartnerList() {
    const raw = this.answers.partner_names;
    if (!raw || typeof raw !== "string") return [];
    const items = raw
      .split(/[\n,;]+/)
      .map((item) => {
        const namePart = item.replace(/\s*-\s*\d{2,3}.*$/, "").trim();
        return namePart || item.trim();
      })
      .filter((item) => item.length > 0);
    return [...new Set(items)];
  },

  get isDirty() {
    if (!this.initialSnapshot) return false;
    return JSON.stringify(this.answers) !== this.initialSnapshot;
  },

  promptLeave(url) {
    if (!this.isDirty) {
      window.location.href = url;
      return;
    }
    this.pendingNavigationUrl = url;
    this.showUnsavedModal = true;
  },

  saveAndLeave() {
    this.isSubmitting = true;
    const form = this.$root.querySelector("form");
    if (!form) {
      window.location.href = this.pendingNavigationUrl;
      return;
    }
    let nextInput = form.querySelector('input[name="next"]');
    if (!nextInput) {
      nextInput = document.createElement("input");
      nextInput.type = "hidden";
      nextInput.name = "next";
      form.appendChild(nextInput);
    }
    nextInput.value = this.pendingNavigationUrl;

    let actionInput = form.querySelector('input[name="action"][type="hidden"]');
    if (!actionInput) {
      actionInput = document.createElement("input");
      actionInput.type = "hidden";
      actionInput.name = "action";
      form.appendChild(actionInput);
    }
    actionInput.value = "save";
    form.submit();
  },

  discardAndLeave() {
    this.isSubmitting = true;
    window.location.href = this.pendingNavigationUrl;
  },
});
