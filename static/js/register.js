const state = {
  step: 1,
  allergies: [],
  conditions: [],
  location: null,
};

const stepContent = {
  1: {
    label: "ขั้นตอนที่ 1 จาก 3",
    title: "สร้างบัญชีของคุณ",
    subtitle: "ใช้เวลาเพียง 2-3 นาที",
    action: "ดำเนินการต่อ",
  },
  2: {
    label: "ขั้นตอนที่ 2 จาก 3",
    title: "ข้อมูลสุขภาพที่สำคัญ",
    subtitle: "ช่วยให้ทีมดูแลคุณได้อย่างปลอดภัย",
    action: "บันทึกและดำเนินการต่อ",
  },
  3: {
    label: "ขั้นตอนที่ 3 จาก 3",
    title: "ร้านยาใกล้คุณ",
    subtitle: "เปิดใช้ตำแหน่งเพื่อผลลัพธ์ที่แม่นยำ",
    action: "สมัครสมาชิก",
  },
};

const form = document.querySelector("#registration-form");
const nextButton = document.querySelector("#next-button");
const backButton = document.querySelector("#back-button");
const formActions = document.querySelector("#form-actions");
const stepLabel = document.querySelector("#step-label");
const formTitle = document.querySelector("#form-title");
const formSubtitle = document.querySelector("#form-subtitle");
const progress = document.querySelector(".progress-steps");
const loginLink = document.querySelector(".login-link");
const toast = document.querySelector("#toast");

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function setError(fieldId, message) {
  const error = document.querySelector(`#${fieldId}-error`);
  const input = document.querySelector(`#${fieldId}`);
  if (error) error.textContent = message;
  if (input) input.classList.toggle("invalid", Boolean(message));
}

function validateStep(step) {
  if (step === 1) {
    const email = document.querySelector("#email");
    const password = document.querySelector("#password");
    const consent = document.querySelector("#consent");
    let valid = true;

    setError("email", "");
    setError("password", "");
    setError("consent", "");

    if (!email.value.trim() || !email.checkValidity()) {
      setError("email", "กรุณากรอกอีเมลที่ถูกต้อง");
      valid = false;
    }

    if (password.value.length < 8) {
      setError("password", "รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร");
      valid = false;
    }

    if (!consent.checked) {
      setError("consent", "กรุณายอมรับข้อกำหนดก่อนดำเนินการต่อ");
      valid = false;
    }

    if (!valid) {
      document.querySelector(".invalid")?.focus();
    }
    return valid;
  }

  if (step === 2) {
    const noAllergies = document.querySelector("#no-allergies");
    setError("allergies", "");
    if (!state.allergies.length && !noAllergies.checked) {
      setError(
        "allergies",
        "โปรดระบุยาที่แพ้ หรือเลือก “ไม่มีประวัติแพ้ยาที่ทราบ”",
      );
      return false;
    }
  }

  return true;
}

function updateStep() {
  document.querySelectorAll(".form-step").forEach((panel) => {
    panel.classList.toggle("active", Number(panel.dataset.step) === state.step);
  });

  document.querySelectorAll(".step").forEach((stepButton, index) => {
    const stepNumber = index + 1;
    stepButton.classList.toggle("active", stepNumber === state.step);
    stepButton.classList.toggle("completed", stepNumber < state.step);
  });

  if (state.step <= 3) {
    const content = stepContent[state.step];
    stepLabel.textContent = content.label;
    formTitle.textContent = content.title;
    formSubtitle.textContent = content.subtitle;
    nextButton.childNodes[0].textContent = `${content.action} `;
    backButton.style.display = state.step === 1 ? "none" : "inline-flex";
    formActions.style.display = "flex";
    progress.style.display = "flex";
    loginLink.style.display = "block";
  } else {
    stepLabel.textContent = "ตั้งค่าบัญชีเรียบร้อย";
    formTitle.textContent = "บัญชีของคุณพร้อมใช้งาน";
    formSubtitle.textContent = "ข้อมูลทั้งหมดถูกบันทึกในอุปกรณ์นี้สำหรับการสาธิต";
    formActions.style.display = "none";
    progress.style.display = "none";
    loginLink.style.display = "none";
  }

  document.querySelector(".form-card").scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
}

function addTag(type, value) {
  const normalized = value.trim();
  if (!normalized || state[type].includes(normalized)) return;
  state[type].push(normalized);
  renderTags(type);
}

function renderTags(type) {
  const list = document.querySelector(`#${type}-list`);
  list.replaceChildren(
    ...state[type].map((value) => {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.append(document.createTextNode(value));

      const remove = document.createElement("button");
      remove.type = "button";
      remove.setAttribute("aria-label", `ลบ ${value}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        state[type] = state[type].filter((item) => item !== value);
        renderTags(type);
      });
      tag.append(remove);
      return tag;
    }),
  );
}

function completeRegistration() {
  const payload = {
    email: document.querySelector("#email").value,
    password: document.querySelector("#password").value,
    allergies: state.allergies,
    conditions: state.conditions,
    location: state.location,
  };

  nextButton.disabled = true;
  nextButton.childNodes[0].textContent = "กำลังสร้างบัญชี... ";

  fetch("/api/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(async (response) => {
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.message || "ไม่สามารถสร้างบัญชีได้");
      }
      window.location.href = result.redirect;
    })
    .catch((error) => {
      nextButton.disabled = false;
      nextButton.childNodes[0].textContent = "สมัครสมาชิก ";
      showToast(error.message);
    });
}

nextButton.addEventListener("click", () => {
  if (!validateStep(state.step)) return;
  if (state.step === 3) {
    completeRegistration();
    return;
  }
  state.step += 1;
  updateStep();
});

backButton.addEventListener("click", () => {
  if (state.step > 1) {
    state.step -= 1;
    updateStep();
  }
});

document.querySelectorAll("[data-step-target]").forEach((stepButton) => {
  stepButton.addEventListener("click", () => {
    const target = Number(stepButton.dataset.stepTarget);
    if (target < state.step) {
      state.step = target;
      updateStep();
    }
  });
});

document.querySelectorAll("[data-add-tag]").forEach((button) => {
  const type = button.dataset.addTag;
  const input = document.querySelector(
    type === "allergies" ? "#allergy-input" : "#condition-input",
  );
  const submitTag = () => {
    addTag(type, input.value);
    input.value = "";
    if (type === "allergies" && state.allergies.length) {
      document.querySelector("#no-allergies").checked = false;
      setError("allergies", "");
    }
  };
  button.addEventListener("click", submitTag);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submitTag();
    }
  });
});

document.querySelector("#no-allergies").addEventListener("change", (event) => {
  if (event.target.checked) {
    state.allergies = [];
    renderTags("allergies");
    setError("allergies", "");
  }
  document.querySelector("#allergy-input").disabled = event.target.checked;
  document.querySelector("[data-add-tag='allergies']").disabled =
    event.target.checked;
});

document.querySelector(".password-toggle").addEventListener("click", (event) => {
  const password = document.querySelector("#password");
  const showing = password.type === "text";
  password.type = showing ? "password" : "text";
  event.currentTarget.setAttribute(
    "aria-label",
    showing ? "แสดงรหัสผ่าน" : "ซ่อนรหัสผ่าน",
  );
});

document.querySelector("#password").addEventListener("input", (event) => {
  const value = event.target.value;
  let score = 0;
  if (value.length >= 8) score += 1;
  if (/[A-Z]/.test(value)) score += 1;
  if (/\d/.test(value)) score += 1;
  if (/[^A-Za-z0-9]/.test(value)) score += 1;
  document.querySelector(".password-strength").dataset.score = String(score);
  setError("password", "");
});

document.querySelector("#email").addEventListener("input", () => {
  setError("email", "");
});

document.querySelector("#consent").addEventListener("change", () => {
  setError("consent", "");
});

document.querySelector("#location-button").addEventListener("click", () => {
  const locationButton = document.querySelector("#location-button");
  const status = document.querySelector("#location-status");

  if (!navigator.geolocation) {
    showToast("เบราว์เซอร์นี้ไม่รองรับการระบุตำแหน่ง");
    return;
  }

  locationButton.disabled = true;
  locationButton.childNodes[2].textContent = " กำลังค้นหาตำแหน่ง...";

  navigator.geolocation.getCurrentPosition(
    (position) => {
      state.location = {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      };
      document.querySelector("#location-consent").checked = true;
      status.classList.add("success");
      status.querySelector("strong").textContent = "ระบุตำแหน่งสำเร็จ";
      status.querySelector("div span").textContent =
        `พิกัด ${state.location.latitude.toFixed(4)}, ${state.location.longitude.toFixed(4)}`;
      locationButton.disabled = false;
      locationButton.childNodes[2].textContent = " อัปเดตตำแหน่ง";
      showToast("บันทึกตำแหน่งปัจจุบันแล้ว");
    },
    () => {
      locationButton.disabled = false;
      locationButton.childNodes[2].textContent = " ลองระบุตำแหน่งอีกครั้ง";
      showToast("ไม่สามารถเข้าถึงตำแหน่งได้ คุณสามารถข้ามขั้นตอนนี้ได้");
    },
    {
      enableHighAccuracy: false,
      timeout: 10000,
      maximumAge: 300000,
    },
  );
});

document.querySelector("#location-consent").addEventListener("change", (event) => {
  if (!event.target.checked) {
    state.location = null;
    const status = document.querySelector("#location-status");
    status.classList.remove("success");
    status.querySelector("strong").textContent = "ยังไม่ได้ระบุตำแหน่ง";
    status.querySelector("div span").textContent =
      "คุณสามารถข้ามและเพิ่มภายหลังได้";
  }
});

document.querySelector("#finish-button").addEventListener("click", () => {
  window.location.href = "/dashboard";
});

form.addEventListener("submit", (event) => event.preventDefault());
