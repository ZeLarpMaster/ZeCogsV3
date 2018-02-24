## 1. Download the template files
Download the messages.pot file from each cog you want to translate.

If you don't know how, follow these steps:

1. Navigate to the cog folder you want. ([Example](react_roles))
2. Go into the `locales` folder of that cog.
3. Click on the `messages.pot` file.
4. Click on the Raw button.
5. Right click in the page.
6. Click `Save as...` and save it somewhere on your computer.

## 2. Create a `language.po` file
To do that, I suggest using [poedit](https://poedit.net/). I'll assume you're using that, if not just find the equivalent functionnalities in your own editor.

1. Start poedit.
2. Click `Open` and navigate to the `messages.pot` file you downloaded earlier.
3. Click `Create a new translation` in the bottom section of poedit.
4. Select your language and click Ok.
5. Save the file with the name poedit suggests you somewhere on your computer.

## 3. Translate!
In poedit, just click a string you want to translate and fill in the translation in the bottom section.

**Make sure your translations are consistent between themselves and are accurate.**

Once you're done with a string, press Ctrl+Enter (Windows) to go to the next one.  
Use that opportunity to also save your file (just in case).

When you'll be done with every string, save one last time.

## 4. Submit your translations
1. If you don't have a GitHub account, go create one.
2. Go back to the `locales` folder of the cog on GitHub and click `Create new file`.
3. Enter your translation file's name in the `Name your file...` field.
4. Copy paste your translation file's content in the editor.
5. Write `[cog_name] Added the translation file for <language>` in the first field under `Propose new file`. (Replace `cog_name` with the cog you're translating and `<language>` with the language you're translating onto. Example: `[react_roles] Added the translation file for french`)
6. Click the `Propose new file` button.

## 5. You're done! What next?
I will review your translation (aka Google Translate to see if it's completely unrelated or offensive) and accept your pull request.

You may receive comments on your pull request about things to fix. If that happens, you'll have to edit the file on your fork. If you don't know how to do that, search online and if you can't find how, feel free to ask.

If your translation is accepted, thank you for helping with the translations!
