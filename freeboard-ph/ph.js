/*! @license
 * Freeboard PH Widget Plugin
 * Copyright 2023 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

(() => {
  // PH styles
  freeboard.addStyle('.ph', 'padding: 10px; position: relative;');
  freeboard.addStyle('.ph-title', 'font-weight: bold;');
  freeboard.addStyle('.ph-level', 'position: absolute; right: 10px;');
  freeboard.addStyle('.ph0', 'color: #fff; background-color: #9ea0a4;');
  freeboard.addStyle('.ph1', 'color: #fff; background-color: #cc0000;');
  freeboard.addStyle('.ph2', 'color: #000; background-color: #ff9900;');
  freeboard.addStyle('.ph3', 'color: #000; background-color: #ffef33;');
  freeboard.addStyle('.ph4', 'color: #000; background-color: #c1e534;');
  freeboard.addStyle('.ph5', 'color: #fff; background-color: #6aa84f;');

  // Hack to inject styles to the overall dashboard
  freeboard.addStyle('.tw-value', 'text-align: center; width: 100%;');

  const ONE_MINUTE = 60;
  const ONE_HOUR = ONE_MINUTE * 60;
  const ONE_DAY = ONE_HOUR * 24;

  const PHNames = [
    'unconfigured',
    'configured',
    'improving',
    'acceptable',
    'commendable',
    'exemplary',
  ];

  const Format = {
    NUMBER: 'number',
    PERCENT: 'percentage',
    DURATION: 'duration',
  };

  class PHWidget {
    constructor(settings) {
      this.containerElement_ = $('<div class="ph"></div>');
      this.titleElement_ = $('<div class="ph-title"></div>');
      this.levelElement_ = $('<div class="ph-level"></div>');
      this.dataElement_ = $('<div class="ph-data"></div>');

      $(this.containerElement_)
          .append(this.titleElement_)
          .append(this.levelElement_)
          .append(this.dataElement_);

      this.state_ = {};
      this.settings_ = {};

      this.onSettingsChanged(settings);
    }

    updateState_() {
      this.containerElement_
          .removeClass('ph0')
          .removeClass('ph1')
          .removeClass('ph2')
          .removeClass('ph3')
          .removeClass('ph4')
          .removeClass('ph5');

      const data = this.state_.data;

      const meetsGoingUp = (threshold) => data >= threshold;
      const meetsGoingDown = (threshold) => data < threshold;
      const meets = this.settings_.goingUp ? meetsGoingUp : meetsGoingDown;

      let level = 0;
      if (meets(this.settings_.threshold5)) {
        level = 5;
      } else if (meets(this.settings_.threshold4)) {
        level = 4;
      } else if (meets(this.settings_.threshold3)) {
        level = 3;
      } else if (meets(this.settings_.threshold2)) {
        level = 2;
      } else if (data != null) {
        level = 1;
      }

      this.levelElement_.html(`(PH-${level})`);
      this.levelElement_.attr('title', PHNames[level]);
      this.dataElement_.html(this.format_(data) || '&nbsp;');
      this.containerElement_.addClass(`ph${level}`);
    }

    round_(data) {
      const factor = Math.pow(10, this.settings_.decimalPlaces);
      return Math.round(data * factor) / factor;
    }

    format_(data) {
      if (data == null) {
        return '';
      }

      if (this.settings_.format == Format.PERCENT) {
        return this.round_(data * 100) + "%";
      } else if (this.settings_.format == Format.DURATION) {
        return this.formatDuration_(data);
      } else {
        return this.round_(data) + " " + this.settings_.units;
      }
    }

    formatDuration_(seconds) {
      let value;
      let unit;
      if (seconds < ONE_MINUTE) {
        value = seconds;
        unit = 'seconds';
      } else if (seconds < ONE_HOUR) {
        value = seconds / ONE_MINUTE;
        unit = 'minutes';
      } else if (seconds < ONE_DAY) {
        value = seconds / ONE_HOUR;
        unit = 'hours';
      } else {
        value = seconds / ONE_DAY;
        unit = 'days';
      }

      return this.round_(value) + ' ' + unit;
    }

    render(element) {
      $(element).append(this.containerElement_);
    }

    onSettingsChanged(settings) {
      this.settings_ = settings;
      this.titleElement_.html(this.settings_.title || "");
      this.updateState_();
    }

    onCalculatedValueChanged(settingName, newValue) {
      this.state_[settingName] = newValue;
      this.updateState_();
    }

    onDispose() {}

    getHeight() {
      return 1;
    }
  }

  freeboard.loadWidgetPlugin({
    type_name: "PHIndicator",
    display_name: "PH Indicator",
    settings: [
      {
        name: "title",
        display_name: "Title",
        type: "text",
      },
      {
        name: "data",
        display_name: "Any numeric data",
        type: "calculated",
      },
      {
        name: "format",
        display_name: "Data format",
        type: "option",
        options: [
          Format.NUMBER,
          Format.PERCENT,
          Format.DURATION,
        ],
        default_value: Format.NUMBER,
      },
      {
        name: "decimalPlaces",
        display_name: "# decimal places",
        type: "number",
        default_value: 1,
      },
      {
        name: "units",
        display_name: "Units for number format",
        type: "text",
        default_value: "",
      },
      {
        name: "goingUp",
        display_name: "Increasing values better?",
        type: "boolean",
        default_value: true,
      },
      {
        name: "threshold2",
        display_name: "PH2 threshold",
        type: "number",
      },
      {
        name: "threshold3",
        display_name: "PH3 threshold",
        type: "number",
      },
      {
        name: "threshold4",
        display_name: "PH4 threshold",
        type: "number",
      },
      {
        name: "threshold5",
        display_name: "PH5 threshold",
        type: "number",
      },
    ],
    newInstance: (settings, newInstanceCallback) => {
      newInstanceCallback(new PHWidget(settings));
    },
  });
})();
