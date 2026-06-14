const emailInput = document.querySelector("#login-email");
const passwordInput = document.querySelector("#login-password");

document.querySelectorAll(".demo-account").forEach((button) => {
  button.addEventListener("click", () => {
    emailInput.value = button.dataset.demoEmail;
    passwordInput.value = button.dataset.demoPassword;
    showToast("ใส่ข้อมูลบัญชีเดโมแล้ว กดเข้าสู่ระบบได้เลย");
    document.querySelector(".auth-submit").focus();
  });
});
