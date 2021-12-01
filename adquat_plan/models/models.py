# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from datetime import timedelta
from odoo.exceptions import UserError, ValidationError
import datetime
from dateutil.relativedelta import relativedelta

from pytz import utc

class planningWeek(models.Model):
    _name = 'planning.week'
    _order = "start_date, affected_to, workcenter_id"

    def _compute_total_hours(self):
        for week in self:
            #week.total_hours = round(sum([(ot.duration_expected) for ot in week.ot_ids]), 2)
            week.total_hours = round(sum([task.planned_hours for task in week.task_ids]), 2)

    def _compute_pending_hours(self):
        for week in self:
            week.pending_hours = round(week.capacity_hours - week.total_hours, 2)

    def _compute_occupation(self):
        for week in self:
            week.occupation = week.capacity_hours and round(week.total_hours * 100 / week.capacity_hours, 2) or 0

    def _compute_occupation_prct(self):
        for week in self:
            week.occupation_prct = week.capacity_hours and round(week.total_hours / week.capacity_hours, 2) or 0


    def _compute_end_date(self):
        for week in self:
            week.end_date = week.start_date + timedelta(days=5)


    @api.depends('occupation')
    def _compute_color(self):
        for status in self:
            if status.occupation >= 100:
                status.color = 1
            elif status.occupation >= 75:
                status.color = 2
            elif status.occupation >= 25:
                status.color = 4
            else:
                status.color = 10

    name = fields.Char('Numéro de semaine')

    affected_to = fields.Many2one('res.users', 'Affectation')
    task_ids = fields.One2many('project.task', 'planning_week_id')

    workcenter_id = fields.Many2one('mrp.workcenter')
    ot_ids = fields.One2many('mrp.workorder', 'planning_week_id')

    start_date = fields.Date('Début semaine')
    end_date = fields.Date('Fin semaine', compute='_compute_end_date')
    total_hours = fields.Float('Heures Effectuées', compute='_compute_total_hours')
    pending_hours = fields.Float('Heures Restantes', compute='_compute_pending_hours')
    capacity_hours = fields.Float('Capacité (H)')
    occupation = fields.Float('Occupation', compute='_compute_occupation')
    occupation_prct = fields.Float('Occupation', compute='_compute_occupation_prct')
    color = fields.Integer("Color Index", compute='_compute_color')


    def name_get(self):
        result = []
        if 'show_gantt' in self._context:
            for week in self:
                #result.append((week.id, '%s (%.2f H plannifié)' % (week.name, week.total_hours)))
                result.append((week.id, 'S-%s : %.0f H (%.0f %%)' % (week.name, week.pending_hours, week.occupation)))
            return result
        return super(planningWeek, self).name_get()


    def pick_this_week(self):
        week_wo_id = self._context.get('week_wo_id')
        if week_wo_id:
            self.env['project.task'].browse([week_wo_id]).write({'planning_week_id': self.id,
                                                              'user_ids': [(6, 0, [self.affected_to.id])]
                                                             })

            # self.env['mrp.workorder'].browse([week_wo_id]).write({'planning_week_id': self.id,
            #                                                 #   'workcenter_id': [(6, 0, [self.workcenter_id.id])]
            #                                                  })
        return {'type': 'ir.actions.act_window_close'}

class projectTask(models.Model):
    _inherit = 'project.task'

    planning_week_id = fields.Many2one('planning.week', string="Planning semaine")


    @api.onchange('planning_week_id')
    def _onchange_planning_week_id(self):
        if self.planning_week_id and self.planning_week_id.start_date:
            self.planned_date_begin = self.planning_week_id.start_date
            self.planned_date_end = self.planning_week_id.start_date + relativedelta(days=4)

    def pick_week(self):
        action = self.env["ir.actions.actions"]._for_xml_id("adquat_plan.action_planning_week")
        action['name'] = 'Planifier'
        action['context'] = {}
        action['target'] = "new"
        user_id = self.user_ids and self.user_ids[0].id or False
        if user_id:
            action['context']['search_default_affected_to'] = user_id
        action['context']['week_task_id'] = self.id
        action['context']['no_create'] = True
        action['context']['create'] = False
        # action['context']['actual_user'] = user_id
        #
        action['views'] = [(self.env.ref('adquat_plan.planning_week_list').id, 'tree'),]
        action['context']['show_gantt'] = True
        # action['context']['actual_user'] = user_id

        # action['views'] = [
        #     (self.env.ref('adquat_plan.planning_week_gantt_view').id, 'gantt'),
        # ]
        return action

class projectProject(models.Model):
    _inherit = 'project.project'
    date_atelier = fields.Date('Date Atelier')

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id and self.task_ids:
            self.task_ids.partner_id = self.partner_id

class mrpWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    planning_week_id = fields.Many2one('planning.week',string="Planning semaine")

    def pick_week(self):
        action = self.env["ir.actions.actions"]._for_xml_id("adquat_plan.action_planning_week_gantt")
        action['name'] = 'Planifier'
        action['context'] = {}
        action['target'] = "new"
        action['context']['search_default_workcenter_id'] = self.workcenter_id.id
        action['context']['week_wo_id'] = self.id
        action['context']['no_create'] = True
        action['context']['create'] = False
        action['context']['show_gantt'] = True
        # action['context']['actual_user'] = user_id

        action['views'] = [
            (self.env.ref('adquat_plan.planning_week_gantt_view').id, 'gantt'),
        ]
        return action

class MrpProduction(models.Model):
    _inherit = "mrp.production"

    def _plan_workorders(self, replan=False,):
        """ Plan all the production's workorders depending on the workcenters
        work schedule.

        :param replan: If it is a replan, only ready and pending workorder will be taken into account
        :type replan: bool.
        """
        self.ensure_one()

        if not self.workorder_ids:
            return
        # Schedule all work orders (new ones and those already created)
        qty_to_produce = max(self.product_qty - self.qty_produced, 0)
        qty_to_produce = self.product_uom_id._compute_quantity(qty_to_produce, self.product_id.uom_id)
        start_date = max(self.date_planned_start, datetime.datetime.now())
        if replan:
            workorder_ids = self.workorder_ids.filtered(lambda wo: wo.state in ('pending', 'waiting', 'ready'))
            # We plan the manufacturing order according to its `date_planned_start`, but if
            # `date_planned_start` is in the past, we plan it as soon as possible.
            workorder_ids.leave_id.unlink()
        else:
            workorder_ids = self.workorder_ids.filtered(lambda wo: not wo.date_planned_start)
        for workorder in workorder_ids:
            workcenters = workorder.workcenter_id | workorder.workcenter_id.alternative_workcenter_ids

            best_finished_date = datetime.datetime.max
            vals = {}
            for workcenter in workcenters:
                # compute theoretical duration
                if workorder.workcenter_id == workcenter:
                    duration_expected = workorder.duration_expected
                else:
                    duration_expected = workorder._get_duration_expected(alternative_workcenter=workcenter)

                if workorder.planning_week_id:
                    force_start_date = datetime.datetime.combine(workorder.planning_week_id.start_date,
                                                                 datetime.datetime.min.time())

                else:
                    force_start_date = start_date

                from_date, to_date = workcenter._get_first_available_slot(force_start_date, duration_expected)
                # If the workcenter is unavailable, try planning on the next one
                if not from_date:
                    continue
                # Check if this workcenter is better than the previous ones
                if to_date and to_date < best_finished_date:
                    best_start_date = from_date
                    best_finished_date = to_date
                    best_workcenter = workcenter
                    vals = {
                        'workcenter_id': workcenter.id,
                        'duration_expected': duration_expected,
                    }
            # Recherche de la bonne planning week
            if vals.get('workcenter_id'):
                plan = self.env['planning.week'].search([('workcenter_id', '=', best_workcenter.id),
                                                            ('start_date','<=', best_start_date)],
                                                            order="start_date DESC", limit=1)
                workorder.planning_week_id = plan.id

            # If none of the workcenter are available, raise
            if best_finished_date == datetime.datetime.max:
                raise UserError(_('Impossible to plan the workorder. Please check the workcenter availabilities.'))

            # Instantiate start_date for the next workorder planning
            if workorder.next_work_order_id:
                start_date = best_finished_date

            # Create leave on chosen workcenter calendar
            leave = self.env['resource.calendar.leaves'].create({
                'name': workorder.display_name,
                'calendar_id': best_workcenter.resource_calendar_id.id,
                'date_from': best_start_date,
                'date_to': best_finished_date,
                'resource_id': best_workcenter.resource_id.id,
                'time_type': 'other'
            })
            vals['leave_id'] = leave.id
            workorder.write(vals)
        self.with_context(force_date=True).write({
            'date_planned_start': self.workorder_ids[0].date_planned_start,
            'date_planned_finished': self.workorder_ids[-1].date_planned_finished
        })


    def button_unplan(self):
        if any(wo.state == 'done' for wo in self.workorder_ids):
            raise UserError(_("Some work orders are already done, you cannot unplan this manufacturing order."))
        elif any(wo.state == 'progress' for wo in self.workorder_ids):
            raise UserError(_("Some work orders have already started, you cannot unplan this manufacturing order."))

        self.workorder_ids.leave_id.unlink()
        self.workorder_ids.write({
            'date_planned_start': False,
            'date_planned_finished': False,
            'planning_week_id':False,
        })
