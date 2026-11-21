import { languageKey } from "./language";

/**
 * The page's own sentences: the few things it says that neither the form nor
 * SurveyJS has a text for. Everything else on the page is the form's own text or
 * one of SurveyJS's interface strings.
 */
export interface PageMessages {
  /** The form takes no submissions. */
  closed: string;
  /** The form could not be fetched. */
  unavailable: string;
  /** The form changed while it was being filled in; said on the new version. */
  changed: string;
  /** The name of the language picker. */
  language: string;
}

/** By language tag, in lower case. A language without an entry reads English. */
const MESSAGES: Readonly<Record<string, PageMessages>> = {
  en: {
    closed: "This form is not taking responses.",
    unavailable: "The form could not be loaded. Try again in a moment.",
    changed: "The form changed while you were filling it in. Your answers are kept; please check them and send again.",
    language: "Language",
  },
  ar: {
    closed: "هذا النموذج لا يستقبل ردودًا.",
    unavailable: "تعذّر تحميل النموذج. حاول مرة أخرى بعد قليل.",
    changed: "تغيّر النموذج أثناء تعبئته. تم الاحتفاظ بإجاباتك؛ يُرجى مراجعتها ثم الإرسال مرة أخرى.",
    language: "اللغة",
  },
  ca: {
    closed: "Aquest formulari no accepta respostes.",
    unavailable: "No s'ha pogut carregar el formulari. Torna-ho a provar d'aquí a un moment.",
    changed: "El formulari ha canviat mentre l'omplies. Les teves respostes s'han conservat; revisa-les i torna'l a enviar.",
    language: "Idioma",
  },
  cs: {
    closed: "Tento formulář nepřijímá odpovědi.",
    unavailable: "Formulář se nepodařilo načíst. Zkuste to za chvíli znovu.",
    changed: "Formulář se během vyplňování změnil. Vaše odpovědi zůstaly zachovány; zkontrolujte je a odešlete znovu.",
    language: "Jazyk",
  },
  da: {
    closed: "Denne formular tager ikke imod svar.",
    unavailable: "Formularen kunne ikke indlæses. Prøv igen om et øjeblik.",
    changed: "Formularen blev ændret, mens du udfyldte den. Dine svar er bevaret; gennemgå dem, og send igen.",
    language: "Sprog",
  },
  de: {
    closed: "Dieses Formular nimmt keine Antworten entgegen.",
    unavailable: "Das Formular konnte nicht geladen werden. Versuchen Sie es gleich noch einmal.",
    changed: "Das Formular wurde geändert, während Sie es ausgefüllt haben. Ihre Antworten bleiben erhalten; bitte prüfen Sie sie und senden Sie erneut.",
    language: "Sprache",
  },
  el: {
    closed: "Αυτή η φόρμα δεν δέχεται απαντήσεις.",
    unavailable: "Δεν ήταν δυνατή η φόρτωση της φόρμας. Δοκιμάστε ξανά σε λίγο.",
    changed: "Η φόρμα άλλαξε ενώ τη συμπληρώνατε. Οι απαντήσεις σας διατηρήθηκαν· ελέγξτε τις και στείλτε ξανά.",
    language: "Γλώσσα",
  },
  es: {
    closed: "Este formulario no está aceptando respuestas.",
    unavailable: "No se pudo cargar el formulario. Inténtalo de nuevo en un momento.",
    changed: "El formulario cambió mientras lo rellenabas. Tus respuestas se han conservado; revísalas y envíalo de nuevo.",
    language: "Idioma",
  },
  fi: {
    closed: "Tämä lomake ei ota vastaan vastauksia.",
    unavailable: "Lomaketta ei voitu ladata. Yritä hetken kuluttua uudelleen.",
    changed: "Lomake muuttui täyttäessäsi sitä. Vastauksesi ovat tallessa; tarkista ne ja lähetä uudelleen.",
    language: "Kieli",
  },
  fr: {
    closed: "Ce formulaire n'accepte pas de réponses.",
    unavailable: "Le formulaire n'a pas pu être chargé. Réessayez dans un instant.",
    changed: "Le formulaire a changé pendant que vous le remplissiez. Vos réponses sont conservées ; vérifiez-les et envoyez à nouveau.",
    language: "Langue",
  },
  he: {
    closed: "הטופס הזה אינו מקבל תשובות.",
    unavailable: "לא ניתן היה לטעון את הטופס. נסו שוב בעוד רגע.",
    changed: "הטופס השתנה בזמן שמילאתם אותו. התשובות שלכם נשמרו; בדקו אותן ושלחו שוב.",
    language: "שפה",
  },
  hu: {
    closed: "Ez az űrlap nem fogad válaszokat.",
    unavailable: "Az űrlapot nem sikerült betölteni. Próbálja újra egy pillanat múlva.",
    changed: "Az űrlap megváltozott, miközben kitöltötte. A válaszai megmaradtak; ellenőrizze őket, és küldje el újra.",
    language: "Nyelv",
  },
  id: {
    closed: "Formulir ini tidak menerima tanggapan.",
    unavailable: "Formulir tidak dapat dimuat. Coba lagi sebentar lagi.",
    changed: "Formulir berubah saat Anda mengisinya. Jawaban Anda tetap disimpan; periksa kembali, lalu kirim lagi.",
    language: "Bahasa",
  },
  it: {
    closed: "Questo modulo non accetta risposte.",
    unavailable: "Non è stato possibile caricare il modulo. Riprova tra un momento.",
    changed: "Il modulo è cambiato mentre lo compilavi. Le tue risposte sono state conservate; controllale e invia di nuovo.",
    language: "Lingua",
  },
  ja: {
    closed: "このフォームは回答を受け付けていません。",
    unavailable: "フォームを読み込めませんでした。しばらくしてからもう一度お試しください。",
    changed: "入力中にフォームが変更されました。回答は保持されています。内容を確認して、もう一度送信してください。",
    language: "言語",
  },
  ko: {
    closed: "이 양식은 응답을 받지 않습니다.",
    unavailable: "양식을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
    changed: "작성하는 동안 양식이 변경되었습니다. 답변은 유지되었으니 확인한 후 다시 제출해 주세요.",
    language: "언어",
  },
  nb: {
    closed: "Dette skjemaet tar ikke imot svar.",
    unavailable: "Skjemaet kunne ikke lastes inn. Prøv igjen om et øyeblikk.",
    changed: "Skjemaet ble endret mens du fylte det ut. Svarene dine er beholdt; se over dem og send på nytt.",
    language: "Språk",
  },
  nl: {
    closed: "Dit formulier neemt geen antwoorden aan.",
    unavailable: "Het formulier kon niet worden geladen. Probeer het zo meteen opnieuw.",
    changed: "Het formulier is gewijzigd terwijl je het invulde. Je antwoorden zijn bewaard; controleer ze en verstuur opnieuw.",
    language: "Taal",
  },
  pl: {
    closed: "Ten formularz nie przyjmuje odpowiedzi.",
    unavailable: "Nie udało się wczytać formularza. Spróbuj ponownie za chwilę.",
    changed: "Formularz zmienił się w trakcie wypełniania. Twoje odpowiedzi zostały zachowane; sprawdź je i wyślij ponownie.",
    language: "Język",
  },
  pt: {
    closed: "Este formulário não está a aceitar respostas.",
    unavailable: "Não foi possível carregar o formulário. Tente novamente dentro de momentos.",
    changed: "O formulário mudou enquanto o preenchia. As suas respostas foram mantidas; verifique-as e envie novamente.",
    language: "Idioma",
  },
  "pt-br": {
    closed: "Este formulário não está aceitando respostas.",
    unavailable: "Não foi possível carregar o formulário. Tente novamente em instantes.",
    changed: "O formulário mudou enquanto você o preenchia. Suas respostas foram mantidas; confira e envie novamente.",
    language: "Idioma",
  },
  ro: {
    closed: "Acest formular nu primește răspunsuri.",
    unavailable: "Formularul nu a putut fi încărcat. Încercați din nou peste câteva momente.",
    changed: "Formularul s-a schimbat în timp ce îl completați. Răspunsurile dvs. au fost păstrate; verificați-le și trimiteți din nou.",
    language: "Limbă",
  },
  ru: {
    closed: "Эта форма не принимает ответы.",
    unavailable: "Не удалось загрузить форму. Повторите попытку чуть позже.",
    changed: "Форма изменилась, пока вы её заполняли. Ваши ответы сохранены; проверьте их и отправьте ещё раз.",
    language: "Язык",
  },
  sv: {
    closed: "Det här formuläret tar inte emot svar.",
    unavailable: "Formuläret kunde inte läsas in. Försök igen om en stund.",
    changed: "Formuläret ändrades medan du fyllde i det. Dina svar finns kvar; kontrollera dem och skicka igen.",
    language: "Språk",
  },
  tr: {
    closed: "Bu form yanıt kabul etmiyor.",
    unavailable: "Form yüklenemedi. Biraz sonra yeniden deneyin.",
    changed: "Siz doldururken form değişti. Yanıtlarınız korundu; lütfen kontrol edip yeniden gönderin.",
    language: "Dil",
  },
  uk: {
    closed: "Ця форма не приймає відповіді.",
    unavailable: "Не вдалося завантажити форму. Спробуйте ще раз за мить.",
    changed: "Форма змінилася, поки ви її заповнювали. Ваші відповіді збережено; перевірте їх і надішліть ще раз.",
    language: "Мова",
  },
  zh: {
    closed: "此表单目前不接受提交。",
    unavailable: "表单无法加载，请稍后再试。",
    changed: "在您填写期间，表单已更改。您的回答已保留，请检查后重新提交。",
    language: "语言",
  },
  "zh-hant": {
    closed: "此表單目前不接受提交。",
    unavailable: "表單無法載入，請稍後再試。",
    changed: "在您填寫期間，表單已變更。您的回答已保留，請檢查後重新提交。",
    language: "語言",
  },
};

/** The page's sentences in the first of `tags` it has them in, else in English. */
export function pageMessages(tags: readonly (string | null | undefined)[]): PageMessages {
  for (const tag of tags) {
    const key = tag ? languageKey(MESSAGES, tag) : undefined;
    if (key) return MESSAGES[key]!;
  }
  return MESSAGES.en!;
}

/** The language tags the page has its own sentences in. */
export const PAGE_LANGUAGES: readonly string[] = Object.keys(MESSAGES);
