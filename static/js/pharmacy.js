const statusLabels = {
  CONFIRMED: "CONFIRMED",
  READY_FOR_PICKUP: "READY_FOR_PICKUP",
  DELIVERED: "DELIVERED",
};

document.querySelectorAll("[data-next-status]").forEach((button) => {
  button.addEventListener("click", async () => {
    const row = button.closest(".order-row");
    const orderId = row.dataset.orderId;
    const status = button.dataset.nextStatus;

    button.disabled = true;
    button.textContent = "กำลังอัปเดต...";

    try {
      const response = await fetch(`/api/pharmacy/orders/${orderId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message);

      const badge = row.querySelector(".status-badge");
      badge.className = `status-badge ${status.toLowerCase()}`;
      badge.textContent = statusLabels[status];

      if (status === "CONFIRMED") {
        button.dataset.nextStatus = "READY_FOR_PICKUP";
        button.textContent = "เตรียมยาเสร็จ";
      } else if (status === "READY_FOR_PICKUP") {
        button.dataset.nextStatus = "DELIVERED";
        button.textContent = "ส่งมอบแล้ว";
      } else {
        button.remove();
      }
      showToast(`อัปเดต ${orderId} เรียบร้อยแล้ว`);
    } catch (error) {
      button.disabled = false;
      button.textContent = "ลองอีกครั้ง";
      showToast(error.message || "ไม่สามารถอัปเดตสถานะได้");
    }
  });
});
