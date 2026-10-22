export const ru = {
  common: {
    appName: 'FLAGMAN Drive',
  },
  auth: {
    login: {
      title: 'Вход',
      subtitle: 'Введите номер телефона — пришлём код в SMS',
      phoneLabel: 'Номер телефона',
      phoneHint: 'Белорусский мобильный номер, 9 цифр после +375',
      submit: 'Получить код',
    },
    code: {
      title: 'Код из SMS',
      subtitle: 'Отправили код на {{phone}}',
      codeLabel: 'Код',
      submit: 'Войти',
      resend: 'Отправить код ещё раз',
      resendIn: 'Новый код можно запросить через {{time}}',
      changePhone: 'Изменить номер',
    },
    errors: {
      otpInvalid: 'Неверный код. Проверьте и введите ещё раз',
      otpExpired: 'Код больше не действует. Запросите новый',
      rateLimited: 'Слишком много попыток. Попробуйте позже',
      rateLimitedFor: 'Слишком много попыток. Повторите через {{time}}',
      signInUnavailable: 'Вход временно недоступен. Попробуйте позже',
      signInNotAllowed: 'Пилот пока открыт только для тестовых номеров',
      network: 'Нет связи с сервером. Проверьте интернет',
      unknown: 'Что-то пошло не так. Попробуйте ещё раз',
    },
    signOut: 'Выйти',
  },
  home: {
    title: 'Вы вошли',
    subtitle: 'Скоро здесь появится карта с машинами',
  },
} as const;
