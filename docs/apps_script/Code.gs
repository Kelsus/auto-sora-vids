const SETTINGS = {
  spreadsheetId: getEnvVar('SPREADSHEET_ID'),
  sheetName: getEnvVar('SHEET_NAME') || 'Queue',
  endpointUrl: `${getEnvVar('API_BASE_URL')}/jobs`,
  apiKey: getEnvVar('API_KEY'),
  pipelineConfig: getJsonEnvVar('PIPELINE_CONFIG'),
  processedKey: 'dispatched_rows',
  scheduleTriggerProp: 'schedule_trigger_installed',
};

function getEnvVar(name) {
  const scriptProps = PropertiesService.getScriptProperties();

  const props = scriptProps.getProperties();
  return props[name];
}

function getJsonEnvVar(name) {
  const raw = getEnvVar(name);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.error(`[getJsonEnvVar] Failed to parse ${name}: ${error.message}`);
    return null;
  }
}


function processScheduledVideos() {
  const start = new Date();
  console.log(`[processScheduledVideos] Starting run at ${start.toISOString()}`);
  ensureTimeTrigger();
  const ss = SpreadsheetApp.openById(SETTINGS.spreadsheetId);
  const sheet = ss.getSheetByName(SETTINGS.sheetName);
  if (!sheet) {
    const message = `Sheet "${SETTINGS.sheetName}" was not found.`;
    console.error(`[processScheduledVideos] ${message}`);
    throw new Error(message);
  }

  const values = sheet.getDataRange().getValues();
  if (values.length <= 1) {
    console.log('[processScheduledVideos] No data rows found; skipping.');
    return;
  }

  const header = values[0].map((value) => value.toString().trim().toLowerCase());
  const urlIndex = header.indexOf('url');
  const scheduleIndex = header.indexOf('schedule datetime');
  const jobTypeIndex = header.indexOf('job type');
  const driveFolderIndex = header.indexOf('drive folder');
  if (urlIndex === -1 || driveFolderIndex === -1) {
    const message = 'Expected header row with "url", and "drive folder" columns.';
    console.error(`[processScheduledVideos] ${message}`);
    throw new Error(message);
  }

  const scriptProps = PropertiesService.getScriptProperties();
  const processed = new Set(JSON.parse(scriptProps.getProperty(SETTINGS.processedKey) || '[]'));

  const now = new Date();
  const timezone = ss.getSpreadsheetTimeZone();
  const formatTimestamp = (date) => Utilities.formatDate(date, timezone, "yyyy-MM-dd'T'HH:mm:ssXXX");

  for (let row = 1; row < values.length; row += 1) {
    const rowNumber = row + 1;
    if (processed.has(rowNumber)) {
      console.log(`[processScheduledVideos] Row ${rowNumber} already processed; skipping.`);
      continue;
    }

    const url = values[row][urlIndex];
    if (!url) {
      console.log(`[processScheduledVideos] Row ${rowNumber} missing URL; skipping.`);
      continue;
    }

    const jobTypeResult = resolveJobType({
      rawValue: jobTypeIndex === -1 ? '' : values[row][jobTypeIndex],
      columnPresent: jobTypeIndex !== -1,
    });
    if (jobTypeResult.error) {
      console.warn(`[processScheduledVideos] Row ${rowNumber}: ${jobTypeResult.error}`);
      const noteColumn = jobTypeIndex !== -1 ? jobTypeIndex + 1 : scheduleIndex + 1;
      annotateCell(sheet, rowNumber, noteColumn, jobTypeResult.error);
      continue;
    }

    const { jobType, includeInPayload } = jobTypeResult;

    let scheduledAt = null;
    if (jobType === 'SCHEDULED') {
      const scheduleCell = values[row][scheduleIndex];
      scheduledAt = normaliseDate(scheduleCell, timezone);
      if (!scheduledAt) {
        console.warn(`[processScheduledVideos] Row ${rowNumber} has invalid schedule value "${scheduleCell}".`);
        annotateCell(sheet, rowNumber, scheduleIndex + 1, 'Invalid datetime value');
        continue;
      }

      if (scheduledAt < now) {
        console.log(`[processScheduledVideos] Row ${rowNumber} scheduled for future (${scheduledAt.toISOString()}); skipping.`);
        continue;
      }
    }


    const driveFolderRaw = values[row][driveFolderIndex];
    const driveFolder = normaliseDriveFolder(driveFolderRaw);
    if (!driveFolder) {
      const message = 'Drive folder is required.';
      console.warn(`[processScheduledVideos] Row ${rowNumber}: ${message}`);
      annotateCell(sheet, rowNumber, driveFolderIndex + 1, message);
      continue;
    }

    try {
      const payload = createDispatchPayload({
        url,
        jobType,
        includeJobType: includeInPayload,
        scheduledAt,
        driveFolder,
      });

      invokeEndpoint(payload);
      annotateCell(
        sheet,
        rowNumber,
        scheduleIndex + 1,
        `Dispatched (${jobType}) at ${formatTimestamp(now)}`,
      );
      processed.add(rowNumber);
      console.log(`[processScheduledVideos] Row ${rowNumber} dispatched successfully.`);
    } catch (error) {
      console.error(`[processScheduledVideos] Row ${rowNumber} dispatch failed: ${error.message}`);
      annotateCell(sheet, rowNumber, scheduleIndex + 1, `Dispatch failed: ${error.message}`);
    }
  }

  scriptProps.setProperty(SETTINGS.processedKey, JSON.stringify(Array.from(processed)));
  console.log(
    `[processScheduledVideos] Completed run at ${new Date().toISOString()} (start ${start.toISOString()}) — processed ${processed.size} rows.`,
  );
}

function normaliseDate(value, timezone) {
  if (value instanceof Date) {
    return value;
  }

  if (typeof value === 'string') {
    const parsed = new Date(value);
    if (!isNaN(parsed.getTime())) {
      return parsed;
    }
  }

  const parsedSerial = Number(value);
  if (!Number.isNaN(parsedSerial)) {
    return new Date(parsedSerial);
  }

  return null;
}

function annotateCell(sheet, row, column, message) {
  sheet.getRange(row, column).setNote(message);
  console.log(`[annotateCell] (${row},${column}) -> ${message}`);
}

function createDispatchPayload({ url, jobType, includeJobType, scheduledAt, driveFolder }) {
  const payload = {
    url: url.toString().trim(),
  };

  if (includeJobType) {
    payload.job_type = jobType;
  }

  if (jobType === 'SCHEDULED' && scheduledAt instanceof Date) {
    payload.scheduled_datetime = scheduledAt.toISOString();
  }

  const mergedConfig = clonePipelineConfig(SETTINGS.pipelineConfig);
  if (driveFolder) {
    mergedConfig.drive_folder = driveFolder;
  }
  if (Object.keys(mergedConfig).length > 0) {
    payload.pipeline_config = mergedConfig;
  }

  return payload;
}

function invokeEndpoint(payload) {
  console.log(`[invokeEndpoint] Dispatching payload: ${JSON.stringify(payload)}`);
  const headers = {
    'Content-Type': 'application/json',
  };
  if (SETTINGS.apiKey && SETTINGS.apiKey !== 'REPLACE_WITH_API_KEY') {
    headers['x-api-key'] = SETTINGS.apiKey;
  }
  const response = UrlFetchApp.fetch(SETTINGS.endpointUrl, {
    method: 'post',
    headers,
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  if (response.getResponseCode() >= 300) {
    const body = response.getContentText();
    console.error(`[invokeEndpoint] Error ${response.getResponseCode()}: ${body}`);
    throw new Error(`Endpoint returned ${response.getResponseCode()}: ${body}`);
  }
  console.log(`[invokeEndpoint] Success ${response.getResponseCode()}`);
}

function resetDispatchedRows() {
  PropertiesService.getScriptProperties().deleteProperty(SETTINGS.processedKey);
  console.log('[resetDispatchedRows] Cleared dispatched rows property.');
}

function ensureTimeTrigger() {
  const scriptProps = PropertiesService.getScriptProperties();
  if (scriptProps.getProperty(SETTINGS.scheduleTriggerProp)) {
    return;
  }
  const triggers = ScriptApp.getProjectTriggers();
  const hasTrigger = triggers.some((trigger) =>
    trigger.getEventType() === ScriptApp.EventType.CLOCK &&
    trigger.getHandlerFunction() === 'processScheduledVideos'
  );
  if (!hasTrigger) {
    ScriptApp.newTrigger('processScheduledVideos').timeBased().everyMinutes(15).create();
    console.log('[ensureTimeTrigger] Installed 15-minute time based trigger.');
  } else {
    console.log('[ensureTimeTrigger] Time based trigger already present.');
  }
  scriptProps.setProperty(SETTINGS.scheduleTriggerProp, 'true');
}

function onEdit(e) {
  try {
    const range = e && e.range ? e.range.getA1Notation() : 'unknown';
    console.log(`[onEdit] Triggered for range ${range}`);
    processScheduledVideos();
  } catch (error) {
    console.error(`[onEdit] Failed: ${error.message}`);
  }
}

function resolveJobType({ rawValue, columnPresent }) {
  if (!columnPresent) {
    return { jobType: 'SCHEDULED', includeInPayload: false, error: null };
  }

  if (rawValue === null || rawValue === undefined) {
    return { jobType: 'SCHEDULED', includeInPayload: false, error: null };
  }

  const text = rawValue.toString().trim();
  if (!text) {
    return { jobType: 'SCHEDULED', includeInPayload: false, error: null };
  }

  const upper = text.toUpperCase();
  if (upper === 'SCHEDULED' || upper === 'IMMEDIATE') {
    return { jobType: upper, includeInPayload: true, error: null };
  }

  return {
    jobType: null,
    includeInPayload: false,
    error: `Invalid job type "${text}". Expected SCHEDULED or IMMEDIATE.`,
  };
}

function normaliseDriveFolder(value) {
  if (value === null || value === undefined) {
    return null;
  }
  const text = value.toString().trim();
  return text ? text : null;
}

function clonePipelineConfig(baseConfig) {
  if (!baseConfig || typeof baseConfig !== 'object') {
    return {};
  }
  try {
    return JSON.parse(JSON.stringify(baseConfig));
  } catch (error) {
    console.warn(`[clonePipelineConfig] Failed to clone base config: ${error.message}`);
    return {};
  }
}

function onChange(e) {
  try {
    const changeType = e && e.changeType ? e.changeType : 'unknown';
    console.log(`[onChange] Triggered (type=${changeType})`);
    processScheduledVideos();
  } catch (error) {
    console.error(`[onChange] Failed: ${error.message}`);
  }
}

function installScheduleTrigger() {
  ScriptApp.newTrigger('processScheduledVideos').timeBased().everyMinutes(15).create();
  console.log('[installScheduleTrigger] Installed 15-minute time based trigger.');
}
