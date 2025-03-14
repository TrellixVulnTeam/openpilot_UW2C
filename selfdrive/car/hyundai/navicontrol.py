# created by atom
import math
import numpy as np

from common.conversions import Conversions as CV
from selfdrive.car.hyundai.values import Buttons
from common.numpy_fast import clip, interp
from cereal import log
import cereal.messaging as messaging
from common.params import Params

import common.log as trace1
import common.MoveAvg as mvAvg

LaneChangeState = log.LateralPlan.LaneChangeState

class NaviControl():
  def __init__(self):

    self.sm = messaging.SubMaster(['liveNaviData', 'liveENaviData', 'lateralPlan', 'radarState', 'controlsState', 'liveMapData'])

    self.btn_cnt = 0
    self.seq_command = 0
    self.target_speed = 0
    self.set_point = 0
    self.wait_timer2 = 0
    self.wait_timer3 = 0

    self.moveAvg = mvAvg.MoveAvg()

    self.gasPressed_old = 0

    self.map_spdlimit_offset = int(Params().get("OpkrSpeedLimitOffset", encoding="utf8"))
    self.map_spdlimit_offset_option = int(Params().get("OpkrSpeedLimitOffsetOption", encoding="utf8"))
    self.safetycam_decel_dist_gain = int(Params().get("SafetyCamDecelDistGain", encoding="utf8"))

    self.map_speed_block = False
    self.map_speed_dist = 0
    self.map_speed = 0
    self.onSpeedControl = False
    self.onSpeedBumpControl = False
    self.curvSpeedControl = False
    self.cutInControl = False
    self.driverSccSetControl = False
    self.ctrl_speed = 0
    self.vision_curv_speed_c = list(map(int, Params().get("VCurvSpeedC", encoding="utf8").split(',')))
    self.vision_curv_speed_t = list(map(int, Params().get("VCurvSpeedT", encoding="utf8").split(',')))

    self.osm_curv_speed_c = list(map(int, Params().get("OCurvSpeedC", encoding="utf8").split(',')))
    self.osm_curv_speed_t = list(map(int, Params().get("OCurvSpeedT", encoding="utf8").split(',')))
    self.osm_custom_spdlimit_c = list(map(int, Params().get("OSMCustomSpeedLimitC", encoding="utf8").split(',')))
    self.osm_custom_spdlimit_t = list(map(int, Params().get("OSMCustomSpeedLimitT", encoding="utf8").split(',')))

    self.osm_wait_timer = 0
    self.stock_navi_info_enabled = Params().get_bool("StockNaviSpeedEnabled")
    self.osm_speedlimit_enabled = Params().get_bool("OSMSpeedLimitEnable")
    self.speedlimit_decel_off = Params().get_bool("SpeedLimitDecelOff")
    self.curv_decel_option = int(Params().get("CurvDecelOption", encoding="utf8"))
    self.cut_in = False
    self.cut_in_run_timer = 0

    self.drive_routine_on_sl = Params().get_bool("RoutineDriveOn")
    if self.drive_routine_on_sl:
      option_list = list(Params().get("RoutineDriveOption", encoding="utf8"))
      if '1' in option_list:
        self.drive_routine_on_sl = True
      else:
        self.drive_routine_on_sl = False
    try:
      self.roadname_and_sl = Params().get("RoadList", encoding="utf8").strip().splitlines()[1].split(',')
    except:
      self.roadname_and_sl = ""
      pass

    self.decel_on_speedbump = Params().get_bool("OPKRSpeedBump")
    self.navi_sel = int(Params().get("OPKRNaviSelect", encoding="utf8"))

    self.na_timer = 0
    self.t_interval = 7
    self.faststart = False
    self.safetycam_speed = 0

  def update_lateralPlan(self):
    self.sm.update(0)
    path_plan = self.sm['lateralPlan']
    return path_plan

  def button_status(self, CS):
    if not CS.cruise_active or CS.cruise_buttons != Buttons.NONE: 
      self.wait_timer2 = 80 
    elif self.wait_timer2: 
      self.wait_timer2 -= 1
    else:
      return 1
    return 0

  # buttn acc,dec control
  def switch(self, seq_cmd):
      self.case_name = "case_" + str(seq_cmd)
      self.case_func = getattr( self, self.case_name, lambda:"default")
      return self.case_func()

  def reset_btn(self):
      if self.seq_command != 3:
        self.seq_command = 0

  def case_default(self):
      self.seq_command = 0
      return None

  def case_0(self):
      self.btn_cnt = 0
      self.target_speed = self.set_point
      delta_speed = round(self.target_speed - self.VSetDis)
      if delta_speed > 0:
        self.seq_command = 1  # case_1 번으로 이동.
      elif delta_speed < 0:
        self.seq_command = 2  # case_2 번으로 이동.
      return None

  def case_1(self):  # acc
      btn_signal = Buttons.RES_ACCEL
      self.btn_cnt += 1
      if self.target_speed == self.VSetDis:
        self.btn_cnt = 0
        self.seq_command = 3        # case_3 번으로 이동.    
      elif self.btn_cnt > 5:
        self.btn_cnt = 0
        self.seq_command = 3        # case_3 번으로 이동.
      return btn_signal

  def case_2(self):  # dec
      btn_signal = Buttons.SET_DECEL
      self.btn_cnt += 1
      if self.target_speed == self.VSetDis:
        self.btn_cnt = 0
        self.seq_command = 3      # case_3 번으로 이동.      
      elif self.btn_cnt > 5:
        self.btn_cnt = 0
        self.seq_command = 3# case_3 번으로 이동.
      return btn_signal

  def case_3(self):  # None  버튼 off 유지시간. 크르즈 속도제어오류 발생시 아래의 수치를 조금 변경하여 보십시오. 수치가 크면 속도변경이 느려지고, 작으면 빨라집니다.
      btn_signal = None  # Buttons.NONE
      
      self.btn_cnt += 1
      #if self.btn_cnt == 1:
      #  btn_signal = Buttons.NONE
      if self.btn_cnt > self.t_interval:    # 버튼 클릭후 일정시간 기다린다.  (반드시 필요함)
        self.seq_command = 0   # case_0 번으로 이동.  (다음 명령을 실행) 
      return btn_signal

  def ascc_button_control(self, CS, set_speed):
    self.set_point = max(20 if CS.is_set_speed_in_mph else 30, set_speed)
    self.curr_speed = CS.out.vEgo * CV.MS_TO_KPH
    self.VSetDis = round(CS.VSetDis)
    btn_signal = self.switch(self.seq_command)

    return btn_signal

  def get_navi_speed(self, sm, CS, cruiseState_speed):
    cruise_set_speed_kph = cruiseState_speed
    v_ego_kph = CS.out.vEgo * CV.MS_TO_KPH
    v_ego_mph = CS.out.vEgo * CV.MS_TO_MPH
    if self.navi_sel in (0, 1):
      self.liveNaviData = sm['liveNaviData']
    elif self.navi_sel == 3:
      self.liveNaviData = sm['liveENaviData']
    # speedLimit = self.liveNaviData.speedLimit
    # safetyDistance = self.liveNaviData.safetyDistance  #safetyDistance
    # safetySign = self.liveNaviData.safetySign
    #mapValid = self.liveNaviData.mapValid
    #trafficType = self.liveNaviData.trafficType
    
    #if not mapValid or trafficType == 0:
    #  return  cruise_set_speed_kph

    if not self.speedlimit_decel_off:
      if self.osm_speedlimit_enabled and not self.sm['controlsState'].osmOffSpdLimit:  # osm speedlimit
        if self.sm['liveMapData'].speedLimit > 21 or self.sm['liveMapData'].speedLimitAhead > 21:
          # spdTarget = cruiseState_speed
          spdTarget = self.sm['liveMapData'].speedLimit
          if spdTarget == 0 and self.drive_routine_on_sl:
            if self.sm['liveMapData'].currentRoadName in self.roadname_and_sl:
              r_index = self.roadname_and_sl.index(self.sm['liveMapData'].currentRoadName)
              spdTarget = float(self.roadname_and_sl[r_index+1])
          self.map_speed = self.sm['liveMapData'].speedLimitAhead
          self.map_speed_dist = max(0, self.sm['liveMapData'].speedLimitAheadDistance)
          cam_distance_calc = 0
          cam_distance_calc = interp(self.map_speed * CV.MPH_TO_KPH if CS.is_set_speed_in_mph else 1, [30, 60, 110], [2.5, 3.0, 3.7])
          consider_speed = interp((v_ego_kph - self.map_speed * CV.MPH_TO_KPH if CS.is_set_speed_in_mph else 1), [0, 50], [1, 1.8])
          min_control_dist = interp(self.map_speed * CV.MPH_TO_KPH if CS.is_set_speed_in_mph else 1, [30, 110], [40, 250])
          final_cam_decel_start_dist = cam_distance_calc*consider_speed*v_ego_kph * (1 + self.safetycam_decel_dist_gain*0.01)
          if ((21 < self.map_speed < spdTarget) or (21 < self.map_speed and spdTarget == 0)) and self.map_speed_dist != 0:
            if self.map_speed_dist < final_cam_decel_start_dist:
              spdTarget = self.map_speed
            elif self.map_speed_dist < min_control_dist:
              spdTarget = self.map_speed
            elif spdTarget == 0:
              self.onSpeedControl = False
              return cruise_set_speed_kph
          elif spdTarget > 21:
            pass
          else:
            self.onSpeedControl = False
            return cruise_set_speed_kph
          if self.map_spdlimit_offset_option == 0:
            cruise_set_speed_kph = spdTarget + round(spdTarget*0.01*self.map_spdlimit_offset)
          elif self.map_spdlimit_offset_option == 1:
            cruise_set_speed_kph = spdTarget + self.map_spdlimit_offset
          else:
            cruise_set_speed_kph = int(interp(spdTarget, self.osm_custom_spdlimit_c, self.osm_custom_spdlimit_t))
          if cruise_set_speed_kph+1.5 < v_ego_mph and CS.is_set_speed_in_mph and not CS.out.gasPressed:
            self.onSpeedControl = True
          elif cruise_set_speed_kph+1.5 < v_ego_kph and not CS.is_set_speed_in_mph and not CS.out.gasPressed:
            self.onSpeedControl = True
          else:
            self.onSpeedControl = False
        elif self.drive_routine_on_sl:
          if self.sm['liveMapData'].currentRoadName in self.roadname_and_sl:
            r_index = self.roadname_and_sl.index(self.sm['liveMapData'].currentRoadName)
            spdTarget = float(self.roadname_and_sl[r_index+1])
            if self.map_spdlimit_offset_option == 0:
              cruise_set_speed_kph = spdTarget + round(spdTarget*0.01*self.map_spdlimit_offset)
            elif self.map_spdlimit_offset_option == 1:
              cruise_set_speed_kph = spdTarget + self.map_spdlimit_offset
            else:
              cruise_set_speed_kph = int(interp(spdTarget, self.osm_custom_spdlimit_c, self.osm_custom_spdlimit_t))
            if cruise_set_speed_kph+1.5 < v_ego_mph and CS.is_set_speed_in_mph and not CS.out.gasPressed:
              self.onSpeedControl = True
            elif cruise_set_speed_kph+1.5 < v_ego_kph and not CS.is_set_speed_in_mph and not CS.out.gasPressed:
              self.onSpeedControl = True
            else:
              self.onSpeedControl = False
      elif self.decel_on_speedbump and (CS.map_enabled or self.navi_sel == 3) and ((self.liveNaviData.safetySign == 107 and self.navi_sel == 0) \
       or (self.liveNaviData.safetySignCam == 124 and self.navi_sel == 1) or (self.liveNaviData.safetySign == 22 and self.navi_sel == 3)):
        sb_consider_speed = interp((v_ego_kph - (20 if CS.is_set_speed_in_mph else 30)), [0, 25, 50], [1., 1.65, 2.])
        sb_final_decel_start_dist = sb_consider_speed*v_ego_kph
        if self.liveNaviData.safetyDistance < sb_final_decel_start_dist and self.navi_sel == 3: # TMap can use safetyDistance
          cruise_set_speed_kph == 20 if CS.is_set_speed_in_mph else 30
          self.onSpeedBumpControl = True
        elif self.navi_sel in (0,1):
          # cruise_set_speed_kph == 20 if CS.is_set_speed_in_mph else 30
          cruise_set_speed_kph = interp(v_ego_kph, [35, 40, 60, 80], [30, 35, 45, 60]) # Hoya
          self.onSpeedBumpControl = True
        else:
          self.onSpeedBumpControl = False
      elif (CS.map_enabled or self.navi_sel == 3) and self.liveNaviData.speedLimit > 21 and self.liveNaviData.safetySignCam not in (4, 7, 16):  # navi app speedlimit
        self.onSpeedBumpControl = False
        self.map_speed_dist = max(0, self.liveNaviData.safetyDistance - 30)
        self.map_speed = self.liveNaviData.speedLimit
        if self.map_speed_dist > 1250:
          self.map_speed_block = True
        else:
          self.map_speed_block = False
        cam_distance_calc = 0
        cam_distance_calc = interp(self.map_speed, [30, 60, 110], [2.5, 3.0, 3.7])
        consider_speed = interp((v_ego_kph - self.map_speed), [0, 50], [1, 1.8])
        min_control_dist = interp(self.map_speed, [30, 110], [40, 250])
        final_cam_decel_start_dist = cam_distance_calc*consider_speed*v_ego_kph * (1 + self.safetycam_decel_dist_gain*0.01)
        if self.map_speed_dist < final_cam_decel_start_dist:
          spdTarget = self.map_speed
        elif self.map_speed_dist >= final_cam_decel_start_dist and self.map_speed_block:
          spdTarget = self.map_speed
        elif self.map_speed_dist < min_control_dist:
          spdTarget = self.map_speed
        elif self.onSpeedControl and self.map_speed > 21:
          spdTarget = self.map_speed
        else:
          return cruise_set_speed_kph
        if self.map_spdlimit_offset_option == 0:
          cruise_set_speed_kph = spdTarget + round(spdTarget*0.01*self.map_spdlimit_offset)
        elif self.map_spdlimit_offset_option == 1:
          cruise_set_speed_kph = spdTarget + self.map_spdlimit_offset
        else:
          cruise_set_speed_kph = int(interp(spdTarget, self.osm_custom_spdlimit_c, self.osm_custom_spdlimit_t))
        if cruise_set_speed_kph+1.5 < v_ego_mph and CS.is_set_speed_in_mph and not CS.out.gasPressed:
          self.onSpeedControl = True
        elif cruise_set_speed_kph+1.5 < v_ego_kph and not CS.is_set_speed_in_mph and not CS.out.gasPressed:
          self.onSpeedControl = True
        else:
          self.onSpeedControl = False
      elif CS.safety_sign > 21 and self.stock_navi_info_enabled and not (CS.map_enabled or self.navi_sel == 3):  # cat stock navi speedlimit
        self.onSpeedBumpControl = False
        self.map_speed_dist = max(0, CS.safety_dist - int(interp(CS.safety_sign, [30,110], [20,70])))
        self.map_speed = CS.safety_sign
        if CS.safety_block_sl < 150:
          self.map_speed_block = True
        else:
          self.map_speed_block = False
        cam_distance_calc = 0
        cam_distance_calc = interp(self.map_speed, [30, 60, 110], [2.5, 3.0, 3.7])
        consider_speed = interp((v_ego_kph - (self.map_speed * (CV.MPH_TO_KPH if CS.is_set_speed_in_mph else 1))), [0, 50], [1, 1.8])
        min_control_dist = interp(self.map_speed, [30, 110], [40, 250])
        final_cam_decel_start_dist = cam_distance_calc*consider_speed*v_ego_kph * (1 + self.safetycam_decel_dist_gain*0.01)
        if self.map_speed_dist < final_cam_decel_start_dist:
          spdTarget = self.map_speed
        elif self.map_speed_dist >= final_cam_decel_start_dist and self.map_speed_block:
          spdTarget = self.map_speed
        elif self.map_speed_dist < min_control_dist:
          spdTarget = self.map_speed
        elif self.onSpeedControl and self.map_speed > 21:
          spdTarget = self.map_speed
        else:
          self.onSpeedControl = False
          return cruise_set_speed_kph
        if self.map_spdlimit_offset_option == 0:
          cruise_set_speed_kph = spdTarget + round(spdTarget*0.01*self.map_spdlimit_offset)
        elif self.map_spdlimit_offset_option == 1:
          cruise_set_speed_kph = spdTarget + self.map_spdlimit_offset
        else:
          cruise_set_speed_kph = int(interp(spdTarget, self.osm_custom_spdlimit_c, self.osm_custom_spdlimit_t))
        if cruise_set_speed_kph+1.5 < v_ego_mph and CS.is_set_speed_in_mph and not CS.out.gasPressed:
          self.onSpeedControl = True
        elif cruise_set_speed_kph+1.5 < v_ego_kph and not CS.is_set_speed_in_mph and not CS.out.gasPressed:
          self.onSpeedControl = True
        else:
          self.onSpeedControl = False
      else:
        spdTarget = cruise_set_speed_kph
        self.onSpeedControl = False
        self.map_speed = 0
        self.map_speed_dist = 0
        self.map_speed_block = False
        self.onSpeedBumpControl = False
    else:
      spdTarget = cruise_set_speed_kph
      self.onSpeedControl = False
      self.map_speed = 0
      self.map_speed_dist = 0
      self.map_speed_block = False
      self.onSpeedBumpControl = False

    # elif safetyDistance >= 50:
    #   if speedLimit <= 60:
    #     spdTarget = interp(safetyDistance, [50, 600], [ speedLimit, speedLimit + 50 ])
    #   else:
    #     spdTarget = interp(safetyDistance, [150, 900], [ speedLimit, speedLimit + 30 ])
    # else:
    #   spdTarget = speedLimit

    # if v_ego_kph < speedLimit:
    #   v_ego_kph = speedLimit

    # print('cruise_set_speed_kph={}'.format(cruise_set_speed_kph))

    return cruise_set_speed_kph

  def auto_speed_control(self, CS, navi_speed, path_plan):
    modelSpeed = path_plan.modelSpeed
    min_control_speed = 20 if CS.is_set_speed_in_mph else 30
    var_speed = navi_speed
    self.lead_0 = self.sm['radarState'].leadOne
    self.lead_1 = self.sm['radarState'].leadTwo
    #self.leadv3 = self.sm['modelV2'].leadsV3

    self.cut_in = True if self.lead_1.status and (self.lead_0.dRel - self.lead_1.dRel) > 2.0 else False
    #cut_in_model = True if self.leadv3[1].prob > 0.5 and abs(self.leadv3[1].x[0] - self.leadv3[0].x[0]) > 3.0 else False

    self.cutInControl = False
    self.driverSccSetControl = False

    if CS.driverAcc_time and CS.cruise_set_mode in (1,2,4):
      self.t_interval = 7
      self.driverSccSetControl = True
      return min(CS.clu_Vanz + (3 if CS.is_set_speed_in_mph else 5), navi_speed)
    # elif self.gasPressed_old:
    #   clu_Vanz = CS.clu_Vanz
    #   ctrl_speed = max(min_control_speed, ctrl_speed, clu_Vanz)
    #   CS.set_cruise_speed(ctrl_speed)
    elif CS.CP.resSpeed > 21:
      self.t_interval = 7
      res_speed = max(min_control_speed, CS.CP.resSpeed)
      return min(res_speed, navi_speed)
    elif CS.cruise_set_mode in (1,2,4):
      if CS.out.brakeLights and CS.out.vEgo == 0:
        self.faststart = True
        var_speed = min(navi_speed, 30 if CS.is_set_speed_in_mph else 45)
      elif self.onSpeedBumpControl:
        var_speed = min(navi_speed, 20 if CS.is_set_speed_in_mph else 30)
        self.t_interval = 7
      elif self.faststart and CS.CP.vFuture <= 40:
        var_speed = min(navi_speed, 30 if CS.is_set_speed_in_mph else 45)
      elif self.lead_0.status and CS.CP.vFuture >= (min_control_speed-(4 if CS.is_set_speed_in_mph else 7)):
        self.faststart = False
        dRel = int(self.lead_0.dRel)
        vRel = int(self.lead_0.vRel * (CV.MS_TO_MPH if CS.is_set_speed_in_mph else CV.MS_TO_KPH))
        if self.cut_in_run_timer > 0:
          self.cut_in_run_timer -= 1
        elif self.cut_in:
          self.cut_in_run_timer = 1000
        if self.cut_in_run_timer and dRel < CS.clu_Vanz * 0.3: # keep decel when cut_in, max running time 10sec
          self.cutInControl = True
          var_speed = min(CS.CP.vFutureA, navi_speed)
        elif vRel >= (-3 if CS.is_set_speed_in_mph else -5):
          var_speed = min(CS.CP.vFuture + max(0, int(dRel*(0.11 if CS.is_set_speed_in_mph else 0.16)+vRel)), navi_speed)
          ttime = 70 if CS.is_set_speed_in_mph else 40
          self.t_interval = int(interp(dRel, [15, 50], [7, ttime])) if not (self.onSpeedControl or self.curvSpeedControl or self.cut_in) else 7
        else:
          var_speed = min(CS.CP.vFuture, navi_speed)
          self.t_interval = 10 if CS.is_set_speed_in_mph else 7
      elif self.lead_0.status and CS.CP.vFuture < min_control_speed:
        self.faststart = False
        var_speed = min(CS.CP.vFuture, navi_speed)
        self.t_interval = 10 if CS.is_set_speed_in_mph else 7
      else:
        self.faststart = False
        var_speed = navi_speed
        ttime = 70 if CS.is_set_speed_in_mph else 40
        self.t_interval = ttime if not (self.onSpeedControl or self.curvSpeedControl or self.cut_in) else 7
    else:
      var_speed = navi_speed
      self.t_interval = 10 if CS.is_set_speed_in_mph else 7

    if CS.cruise_set_mode in (1,3,4) and self.curv_decel_option in (1,2):
      if CS.out.vEgo * CV.MS_TO_KPH > 40 and modelSpeed < self.vision_curv_speed_c[-1] and path_plan.laneChangeState == LaneChangeState.off and \
       not (CS.out.leftBlinker or CS.out.rightBlinker):
        if CS.is_set_speed_in_mph:
          v_curv_speed = int(interp(modelSpeed, self.vision_curv_speed_c, self.vision_curv_speed_t)/2)*2
        else:
          v_curv_speed = int(interp(modelSpeed, self.vision_curv_speed_c, self.vision_curv_speed_t)/3)*3
        v_curv_speed = min(var_speed, v_curv_speed) # curve speed ratio
      else:
        v_curv_speed = 255
    else:
      v_curv_speed = 255

    if CS.cruise_set_mode in (1,3,4) and self.curv_decel_option in (1,3):
      if self.sm['liveMapData'].turnSpeedLimitEndDistance > 30:
        o_curv_speed = int(interp(self.sm['liveMapData'].turnSpeedLimit, self.osm_curv_speed_c, self.osm_curv_speed_t))
        self.osm_wait_timer += 1 if modelSpeed > self.vision_curv_speed_c[-1] else 0
        if self.osm_wait_timer > 100:
          o_curv_speed = 255
      else:
        o_curv_speed = 255
        self.osm_wait_timer = 0
    else:
      o_curv_speed = 255
      self.osm_wait_timer = 0

    # self.gasPressed_old = CS.gasPressed
    if var_speed > round(min(v_curv_speed, o_curv_speed)):
      v_ego_kph = CS.out.vEgo * CV.MS_TO_KPH
      if round(min(v_curv_speed, o_curv_speed))+1 < v_ego_kph and not CS.out.gasPressed:
        self.curvSpeedControl = True
      else:
        self.curvSpeedControl = False
    else:
      self.curvSpeedControl = False

    return round(min(var_speed, v_curv_speed, o_curv_speed))

  def update(self, CS, path_plan):
    self.na_timer += 1
    if self.na_timer > 100:
      self.na_timer = 0
      self.speedlimit_decel_off = Params().get_bool("SpeedLimitDecelOff")
    btn_signal = None
    if not self.button_status(CS):  # 사용자가 버튼클릭하면 일정시간 기다린다.
      pass
    elif CS.cruise_active:
      cruiseState_speed = round(self.sm['controlsState'].vCruise)
      kph_set_vEgo = self.get_navi_speed(self.sm, CS, cruiseState_speed) # camspeed
      if self.osm_speedlimit_enabled:
        navi_speed = kph_set_vEgo
      else:
        navi_speed = min(cruiseState_speed, kph_set_vEgo)
      self.safetycam_speed = navi_speed
      if CS.cruise_set_mode != 5:
        self.ctrl_speed = self.auto_speed_control(CS, navi_speed, path_plan) # lead, curve speed
      else:
        self.ctrl_speed = navi_speed # navi speed

      # print('self.ctrl_speed={}  cruiseState_speed={}'.format(self.ctrl_speed, cruiseState_speed))      

      btn_signal = self.ascc_button_control(CS, self.ctrl_speed)

    return btn_signal
