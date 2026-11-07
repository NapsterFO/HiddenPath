# Подпись Hidden Path через SignPath Foundation

Инструкция для владельца репозитория. Пользователям программы это читать не нужно.

SignPath Foundation бесплатно подписывает программы с открытым кодом. Издателем
в подписи будет «SignPath Foundation». Подписываются только файлы, собранные
на GitHub Actions из этого репозитория (`.github/workflows/release.yml`).

## Что уже готово в репозитории

- Лицензия MIT (`LICENSE`).
- Сборка на GitHub Actions: при публикации релиза собирается `HiddenPath.exe`
  и `HiddenPath-Setup.exe`, установщик выкладывается в релиз.
- В exe и установщике есть сведения о программе: название `Hidden Path`,
  версия, `NapsterFO`, описание, copyright (`tools/version_info.py`, `installer.iss`).
- В README есть раздел «Code signing policy» с обязательной фразой SignPath,
  ролями и ссылкой на описание сетевых подключений.

## Что сделать вам

1. **Включить двухфакторную авторизацию на GitHub**, если ещё не включена:
   https://github.com/settings/security. SignPath требует её у всей команды.
2. **Подать заявку:** https://signpath.org/apply. Ссылка на проект —
   https://github.com/NapsterFO/HiddenPath. Если спросят про сборку — GitHub Actions,
   `windows-latest`, файл `.github/workflows/release.yml`.
3. **Ждать одобрения.** Его дают вручную, это может занять от нескольких дней.
4. **После одобрения в SignPath** (https://app.signpath.io):
   - в организацию добавить Trusted Build System **GitHub.com** и привязать к проекту;
   - установить SignPath GitHub App на репозиторий HiddenPath (рекомендуют для проверок);
   - сделать две конфигурации артефактов (Artifact configurations): для exe и для установщика.
     Черновики ниже;
   - создать API-токен пользователя с правом submitter.
5. **В GitHub → Settings → Secrets and variables → Actions:**
   - секрет `SIGNPATH_API_TOKEN` — токен из SignPath;
   - переменные (вкладка Variables):
     - `SIGNPATH_ORGANIZATION_ID` — ID организации;
     - `SIGNPATH_PROJECT_SLUG` — slug проекта;
     - `SIGNPATH_POLICY_SLUG` — slug политики подписи (обычно `release-signing`);
     - `SIGNPATH_EXE_CONFIG` — slug конфигурации для exe;
     - `SIGNPATH_SETUP_CONFIG` — slug конфигурации для установщика.

   Как только `SIGNPATH_ORGANIZATION_ID` задана, следующий релиз соберётся с подписью.
   На каждый релиз придут **две** заявки на подпись (exe, затем установщик) —
   их нужно одобрить в SignPath, сборка ждёт до часа на каждую.

## Черновики конфигураций артефактов

Проверьте синтаксис по документации SignPath (Artifact configuration → reference)
перед сохранением: эти черновики не проверены в самом SignPath.

GitHub Actions передаёт файл в ZIP, поэтому корень — `<zip-file>`.

Для exe (`SIGNPATH_EXE_CONFIG`):

```xml
<?xml version="1.0" encoding="utf-8"?>
<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <zip-file>
    <pe-file path="HiddenPath.exe" product-name="Hidden Path">
      <authenticode-sign />
    </pe-file>
  </zip-file>
</artifact-configuration>
```

Для установщика (`SIGNPATH_SETUP_CONFIG`):

```xml
<?xml version="1.0" encoding="utf-8"?>
<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <zip-file>
    <pe-file path="HiddenPath-Setup.exe" product-name="Hidden Path">
      <authenticode-sign />
    </pe-file>
  </zip-file>
</artifact-configuration>
```

Внимание: Inno Setup дополняет строки сведений установщика пробелами до
фиксированной длины («Hidden Path␣␣␣…»). Если SignPath отклонит установщик из-за
`product-name`, уберите это ограничение в конфигурации установщика или сравните
значение без пробелов.

## Как выпускать релиз после этого

1. Поднять версию в `zapret_manager.py` (`APP_VERSION`) и `installer.iss` (`AppVersion`), закоммитить, запушить.
2. `gh release create vX.Y.Z --title "Hidden Path X.Y.Z" --notes "…"` — без файла установщика.
   В конце заметок к релизу — строка про подпись (требование SignPath):
   `Code signing policy: https://github.com/NapsterFO/HiddenPath#code-signing-policy`
3. GitHub Actions соберёт установщик и выложит его в релиз (вкладка Actions).
   Пока установщика в релизе нет, программа не предлагает обновление.
