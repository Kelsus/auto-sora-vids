# Google Sheets Dispatch Script

This Apps Script watches a Google Sheet for article URLs that should be fed into the `aivideo` pipeline at or after a scheduled timestamp. When a row is due, the script posts the payload to the AWS Lambda API exposed by this project.

## Sheet Layout

Create a sheet (default name `Queue`) with the following header row:

| url | schedule datetime | social network | job type | drive folder |
| --- | --- | --- | --- | --- |

* Columns must use the exact header labels (case insensitive).
* The `job type` column is optional. When omitted or left blank the script defaults to `SCHEDULED` behaviour and omits the field from the payload. Populated cells must be `SCHEDULED` or `IMMEDIATE` (case insensitive).
* The `drive folder` column is required for every row. Its value must match a downstream Google Drive subfolder; the script copies it into `pipeline_config.drive_folder` on dispatch.
* Timestamps can be native Google Sheets datetime values or ISO-8601 strings. The script skips blank rows automatically.
* Scheduled jobs still require a future `schedule datetime`. Immediate jobs ignore the datetime cell but the script will write status notes back to that column for consistency.

## Script Setup

1. Open the sheet and go to **Extensions → Apps Script**.
2. Create a new project and replace the default contents with [`Code.gs`](Code.gs).
3. Capture the Apps Script project ID and Spreadsheet ID (you will set them as script properties in the next step).
4. Configure credentials via **Extensions → Apps Script → Project Settings → Script properties** (see “Environment Variables” below).
5. Save the script and grant permissions on the first run.

### Environment Variables

The automation loads all runtime settings from script properties:

- `SPREADSHEET_ID` *(required)* – ID from the Google Sheet URL (`https://docs.google.com/spreadsheets/d/<ID>/…`).
- `SHEET_NAME` *(optional)* – sheet/tab name. Defaults to `Queue` if omitted.
- `API_BASE_URL` *(required)* – base URL of the deployed API Gateway stage, e.g. `https://uxc155vpv4.execute-api.us-east-1.amazonaws.com/prod`.
- `API_KEY` *(required if your API is protected)* – API Gateway key value; omit if the endpoint is public.
- `PIPELINE_CONFIG` *(optional)* – JSON string with per-job overrides you want every dispatch to include, for example `{ "media_provider": "veo" }`. The helper parses and forwards the object as-is; row-level `drive folder` values merge into this object on dispatch.

To add a script property:

1. In the Apps Script editor, open **Project Settings** (gear icon).
2. Scroll to **Script properties** and click **Add script property**.
3. Enter the key (`SPREADSHEET_ID`, `API_BASE_URL`, `API_KEY`, etc.) and paste the value. For `PIPELINE_CONFIG`, paste valid JSON.
4. Save your changes.

If you ever rotate credentials, update the property value here—no code changes required. For bulk updates you can also run the following snippet once in the Apps Script console:

```javascript
function seedEnv() {
  PropertiesService.getScriptProperties().setProperties({
    API_KEY: 'paste-new-key-here',
  }, true);
}
```

Any of the above options will be read by the script during dispatch. If none exist, the code falls back to the placeholder `REPLACE_WITH_API_KEY`, which will cause authentication failures until you provide a real value.

### Deploying with `clasp`

The repository ships with `scripts/deploy_apps_script.sh` to push changes using [`@google/clasp`](https://github.com/google/clasp):

1. Install clasp globally (`npm install -g @google/clasp`) and authenticate (`clasp login` or `clasp login --creds <service-account.json>`).
2. Inside `docs/apps_script/`, initialize the project once with `clasp create --title "Auto Sora Dispatch" --type standalone`. This writes `.clasp.json` (ignored by git) pointing at your Apps Script project.
3. From the repo root, run `scripts/deploy_apps_script.sh`. The script runs `clasp push --force` and `clasp deploy`, creating a new version with a timestamped description.
4. After the first deployment, open the Apps Script UI to verify triggers and update script properties as needed.

### Scheduling

Run the `installScheduleTrigger` function once from the Apps Script editor. It registers a time-driven trigger that runs `processScheduledVideos` every 15 minutes. Adjust the interval if needed.

### Resetting Processed Rows

If you modify existing rows and want them reprocessed, run the `resetDispatchedRows` function. It clears the doc property that tracks delivered rows; the next scheduled run will re-send any rows whose publish time is due.

### Error Visibility

Any dispatch errors are written as a cell note on the `publish schedule datetime` column for the affected row. Fix the underlying issue (e.g., invalid URL or server error) and run `resetDispatchedRows` to retry.
