document.querySelectorAll('[data-toggle-password]').forEach((button) => {
  button.addEventListener('click', () => {
    const input = button.closest('.password-input').querySelector('input');
    const icon = button.querySelector('i');
    const visible = input.type === 'text';
    input.type = visible ? 'password' : 'text';
    icon.className = visible ? 'bi bi-eye' : 'bi bi-eye-slash';
    button.setAttribute('aria-label', visible ? 'Show password' : 'Hide password');
  });
});

document.querySelectorAll('[data_password_strength]').forEach((input) => {
  const meter = input.closest('.auth-field').querySelector('.password-meter span');
  input.addEventListener('input', () => {
    const value = input.value;
    const score = [value.length >= 8, /[A-Z]/.test(value), /[a-z]/.test(value), /\d/.test(value), /[^A-Za-z0-9\s]/.test(value)].filter(Boolean).length;
    meter.style.width = `${(score / 5) * 100}%`;
    meter.className = score === 5 ? 'strong' : score >= 3 ? 'fair' : '';
  });
});

if (window.intlTelInput) {
  document.querySelectorAll('input[name="phone_number"]').forEach((input) => {
    const phoneInput = window.intlTelInput(input, {
      initialCountry: 'ke',
      preferredCountries: ['ke', 'ug', 'tz', 'rw', 'gb', 'us'],
      separateDialCode: true,
      nationalMode: false,
      formatOnDisplay: true,
      loadUtils: () => import('https://cdn.jsdelivr.net/npm/intl-tel-input@25.12.4/build/js/utils.js'),
    });

    if (input.value.startsWith('+')) {
      phoneInput.setNumber(input.value);
    }

    input.form.addEventListener('submit', (event) => {
      if (!phoneInput.isValidNumber()) {
        event.preventDefault();
        input.setCustomValidity('Enter a valid number for the selected country.');
        input.reportValidity();
        return;
      }
      input.setCustomValidity('');
      input.value = phoneInput.getNumber();
    });

    input.addEventListener('input', () => input.setCustomValidity(''));
    input.addEventListener('countrychange', () => input.setCustomValidity(''));
  });
}
