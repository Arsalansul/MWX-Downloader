# MWX Downloader

Локальный веб-интерфейс для JSON-парсеров Manga Watcher X. Приложение получает
каталоги и метаданные тайтлов, показывает список глав и скачивает выбранные главы
в папки или CBZ-архивы.

## Быстрый запуск на Windows

Требуется Python 3.9 или новее. Внешние Python-пакеты не нужны.

```powershell
.\mwx-web.cmd
```

Приложение выберет свободный порт, откроет браузер и будет слушать только
`127.0.0.1`. Для остановки нажмите `Ctrl+C` в окне сервера.

Другие варианты:

```powershell
.\mwx-web.cmd --port 8765
.\mwx-web.cmd --no-browser
.\mwx-web.cmd --no-cbz --output "D:\Manga"
```

## Как пользоваться

1. Выберите источник.
2. Загрузите каталог либо перейдите на вкладку «Прямая ссылка».
3. Откройте тайтл.
4. Отметьте нужные главы.
5. Нажмите «Скачать выбранное».

### Naver

Встроенные источники `Naver Webtoon` и `Naver Series` работают по прямым
ссылкам, без JSON-файла парсера:

- `comic.naver.com` — метаданные, список глав и скачивание публичных бесплатных
  эпизодов;
- `series.naver.com` — метаданные и полный список выпусков. Прямое скачивание
  отключено, поскольку изображения открываются через защищённый Naver Viewer.

Примеры ссылок:

```text
https://comic.naver.com/challenge/list?titleId=830864
https://series.naver.com/comic/detail.series?productNo=8423030
```

Загрузки по умолчанию сохраняются в `downloads`. Состояние очереди показывается
внизу страницы и обновляется автоматически.

Статус источника берётся из последнего `parser-check.json`. Некоторые частично
рабочие источники поддерживают только прямую ссылку. Встроенные в исходные MWX
JSON-файлы сессионные cookies и токены намеренно удалены из этого репозитория.

## Консольный интерфейс

В проект также входит CLI:

```powershell
.\mwx.cmd sources
.\mwx.cmd info --parser mangabuff --url "https://example/title"
.\mwx.cmd chapter --parser SOURCE --url "https://example/chapter" --series "Title" --name "Chapter 1"
.\mwx.cmd title --parser SOURCE --url "https://example/title"
```

Полное описание команд находится в [MWX-PC.md](MWX-PC.md).

## Тесты

```powershell
python -m unittest discover -s tests -v
```

Тесты используют локальный HTTP-сервер и не обращаются к внешним сайтам.
