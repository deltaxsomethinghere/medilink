const sidebar = document.querySelector("#clinical-sidebar");
const menuButton = document.querySelector(".mobile-menu");
const sidebarOverlay = document.querySelector("#sidebar-overlay");

function closeSidebar() {
  sidebar?.classList.remove("open");
  sidebarOverlay?.classList.remove("open");
  menuButton?.setAttribute("aria-expanded", "false");
}

if (sidebar && menuButton) {
  menuButton.addEventListener("click", () => {
    const open = sidebar.classList.toggle("open");
    sidebarOverlay?.classList.toggle("open", open);
    menuButton.setAttribute("aria-expanded", String(open));
  });

  if (sidebarOverlay) {
    sidebarOverlay.addEventListener("click", closeSidebar);
  }

  document.addEventListener("click", (event) => {
    if (
      sidebar.classList.contains("open") &&
      !sidebar.contains(event.target) &&
      !menuButton.contains(event.target) &&
      event.target !== sidebarOverlay
    ) {
      closeSidebar();
    }
  });
}


document.querySelectorAll("[data-modal-open]").forEach((button) => {
  button.addEventListener("click", () => {
    const modal = document.querySelector(`#${button.dataset.modalOpen}`);
    modal?.classList.add("open");
    modal?.querySelector("button, input, select")?.focus();
  });
});

document.querySelectorAll("[data-modal-close]").forEach((button) => {
  button.addEventListener("click", () => {
    button.closest(".modal-backdrop")?.classList.remove("open");
  });
});

document.querySelectorAll(".modal-backdrop").forEach((modal) => {
  modal.addEventListener("click", (event) => {
    if (event.target === modal) modal.classList.remove("open");
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    document.querySelector(".modal-backdrop.open")?.classList.remove("open");
  }
});

const patientSearch = document.querySelector("#patient-search");
if (patientSearch) {
  patientSearch.addEventListener("input", () => {
    const query = patientSearch.value.trim().toLowerCase();
    document.querySelectorAll("#patient-table tr").forEach((row) => {
      row.hidden = !row.dataset.search.toLowerCase().includes(query);
    });
  });
}

const aiSuggest = document.querySelector("#ai-suggest");
if (aiSuggest) {
  aiSuggest.addEventListener("click", () => {
    const condition = document.querySelector("#ai-condition").value.trim();
    if (!condition) {
      showToast("กรุณาระบุอาการหรือการวินิจฉัยก่อน");
      return;
    }
    aiSuggest.disabled = true;
    aiSuggest.textContent = "กำลังตรวจสอบข้อมูล...";
    window.setTimeout(() => {
      aiSuggest.disabled = false;
      aiSuggest.textContent = "วิเคราะห์ทางเลือก";
      showToast("พบ 3 ทางเลือกที่ไม่ขัดกับประวัติแพ้ยา รอแพทย์ตรวจสอบ");
    }, 900);
  });
}
