// Generated from the live schema. Regenerate after any migration:
//   npm run gen:types
// Do not edit by hand.

export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  __InternalSupabase: { PostgrestVersion: '14.5' }
  public: {
    Tables: {
      area_types: {
        Row: { area_type_id: number; name: string }
        Insert: { area_type_id?: number; name: string }
        Update: { area_type_id?: number; name?: string }
        Relationships: []
      }
      attachments: {
        Row: {
          attachment_id: number
          created_at: string
          file_name: string
          file_size_bytes: number | null
          file_type: string | null
          file_url: string
          ticket_id: number
          uploaded_by: string
        }
        Insert: {
          attachment_id?: number
          created_at?: string
          file_name: string
          file_size_bytes?: number | null
          file_type?: string | null
          file_url: string
          ticket_id: number
          uploaded_by: string
        }
        Update: {
          attachment_id?: number
          created_at?: string
          file_name?: string
          file_size_bytes?: number | null
          file_type?: string | null
          file_url?: string
          ticket_id?: number
          uploaded_by?: string
        }
        Relationships: [
          { foreignKeyName: 'attachments_ticket_id_fkey'; columns: ['ticket_id']; isOneToOne: false; referencedRelation: 'tickets'; referencedColumns: ['ticket_id'] },
          { foreignKeyName: 'attachments_uploaded_by_fkey'; columns: ['uploaded_by']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
        ]
      }
      buildings: {
        Row: {
          building_id: number
          name: string
          latitude: number | null
          longitude: number | null
          map_x: number | null
          map_y: number | null
        }
        Insert: {
          building_id?: number
          name: string
          latitude?: number | null
          longitude?: number | null
          map_x?: number | null
          map_y?: number | null
        }
        Update: {
          building_id?: number
          name?: string
          latitude?: number | null
          longitude?: number | null
          map_x?: number | null
          map_y?: number | null
        }
        Relationships: []
      }
      comments: {
        Row: {
          comment_id: number
          created_at: string
          is_internal: boolean
          message: string
          ticket_id: number
          updated_at: string | null
          user_id: string
        }
        Insert: {
          comment_id?: number
          created_at?: string
          is_internal?: boolean
          message: string
          ticket_id: number
          updated_at?: string | null
          user_id: string
        }
        Update: {
          comment_id?: number
          created_at?: string
          is_internal?: boolean
          message?: string
          ticket_id?: number
          updated_at?: string | null
          user_id?: string
        }
        Relationships: [
          { foreignKeyName: 'comments_ticket_id_fkey'; columns: ['ticket_id']; isOneToOne: false; referencedRelation: 'tickets'; referencedColumns: ['ticket_id'] },
          { foreignKeyName: 'comments_user_id_fkey'; columns: ['user_id']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
        ]
      }
      departments: {
        Row: {
          active: boolean
          created_at: string
          department_id: number
          description: string | null
          name: string
          updated_at: string
        }
        Insert: {
          active?: boolean
          created_at?: string
          department_id?: number
          description?: string | null
          name: string
          updated_at?: string
        }
        Update: {
          active?: boolean
          created_at?: string
          department_id?: number
          description?: string | null
          name?: string
          updated_at?: string
        }
        Relationships: []
      }
      floors: {
        Row: { building_id: number; floor_id: number; floor_number: string }
        Insert: { building_id: number; floor_id?: number; floor_number: string }
        Update: { building_id?: number; floor_id?: number; floor_number?: string }
        Relationships: [
          { foreignKeyName: 'floors_building_id_fkey'; columns: ['building_id']; isOneToOne: false; referencedRelation: 'buildings'; referencedColumns: ['building_id'] },
        ]
      }
      issue_types: {
        Row: {
          default_department_id: number | null
          description: string | null
          issue_type_id: number
          name: string
        }
        Insert: {
          default_department_id?: number | null
          description?: string | null
          issue_type_id?: number
          name: string
        }
        Update: {
          default_department_id?: number | null
          description?: string | null
          issue_type_id?: number
          name?: string
        }
        Relationships: [
          { foreignKeyName: 'issue_types_default_department_id_fkey'; columns: ['default_department_id']; isOneToOne: false; referencedRelation: 'departments'; referencedColumns: ['department_id'] },
        ]
      }
      locations: {
        Row: {
          area_type_id: number | null
          exact_description: string | null
          floor_id: number | null
          latitude: number | null
          location_id: number
          longitude: number | null
          room_number: string | null
        }
        Insert: {
          area_type_id?: number | null
          exact_description?: string | null
          floor_id?: number | null
          latitude?: number | null
          location_id?: number
          longitude?: number | null
          room_number?: string | null
        }
        Update: {
          area_type_id?: number | null
          exact_description?: string | null
          floor_id?: number | null
          latitude?: number | null
          location_id?: number
          longitude?: number | null
          room_number?: string | null
        }
        Relationships: [
          { foreignKeyName: 'locations_floor_id_fkey'; columns: ['floor_id']; isOneToOne: false; referencedRelation: 'floors'; referencedColumns: ['floor_id'] },
          { foreignKeyName: 'locations_area_type_id_fkey'; columns: ['area_type_id']; isOneToOne: false; referencedRelation: 'area_types'; referencedColumns: ['area_type_id'] },
        ]
      }
      notifications: {
        Row: {
          attempts: number
          created_at: string
          delivery_status: string
          error_message: string | null
          last_attempt_at: string | null
          message: string
          notification_id: number
          read_at: string | null
          ticket_id: number | null
          type: string
          user_id: string
        }
        Insert: {
          attempts?: number
          created_at?: string
          delivery_status?: string
          error_message?: string | null
          last_attempt_at?: string | null
          message: string
          notification_id?: number
          read_at?: string | null
          ticket_id?: number | null
          type: string
          user_id: string
        }
        Update: {
          attempts?: number
          created_at?: string
          delivery_status?: string
          error_message?: string | null
          last_attempt_at?: string | null
          message?: string
          notification_id?: number
          read_at?: string | null
          ticket_id?: number | null
          type?: string
          user_id?: string
        }
        Relationships: [
          { foreignKeyName: 'notifications_user_id_fkey'; columns: ['user_id']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
          { foreignKeyName: 'notifications_ticket_id_fkey'; columns: ['ticket_id']; isOneToOne: false; referencedRelation: 'tickets'; referencedColumns: ['ticket_id'] },
        ]
      }
      priorities: {
        Row: { level: number; name: string; priority_id: number }
        Insert: { level: number; name: string; priority_id?: number }
        Update: { level?: number; name?: string; priority_id?: number }
        Relationships: []
      }
      roles: {
        Row: { name: string; role_id: number }
        Insert: { name: string; role_id?: number }
        Update: { name?: string; role_id?: number }
        Relationships: []
      }
      statuses: {
        Row: { name: string; status_id: number }
        Insert: { name: string; status_id?: number }
        Update: { name?: string; status_id?: number }
        Relationships: []
      }
      student_types: {
        Row: { name: string; student_type_id: number }
        Insert: { name: string; student_type_id?: number }
        Update: { name?: string; student_type_id?: number }
        Relationships: []
      }
      ticket_assignments: {
        Row: {
          assigned_at: string
          assigned_by: string
          assigned_to: string
          assignment_id: number
          ticket_id: number
          unassigned_at: string | null
        }
        Insert: {
          assigned_at?: string
          assigned_by: string
          assigned_to: string
          assignment_id?: number
          ticket_id: number
          unassigned_at?: string | null
        }
        Update: {
          assigned_at?: string
          assigned_by?: string
          assigned_to?: string
          assignment_id?: number
          ticket_id?: number
          unassigned_at?: string | null
        }
        Relationships: [
          { foreignKeyName: 'ticket_assignments_ticket_id_fkey'; columns: ['ticket_id']; isOneToOne: false; referencedRelation: 'tickets'; referencedColumns: ['ticket_id'] },
          { foreignKeyName: 'ticket_assignments_assigned_to_fkey'; columns: ['assigned_to']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
          { foreignKeyName: 'ticket_assignments_assigned_by_fkey'; columns: ['assigned_by']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
        ]
      }
      ticket_history: {
        Row: {
          action: string
          changed_by: string | null
          created_at: string
          field_name: string | null
          history_id: number
          new_value: string | null
          old_value: string | null
          ticket_id: number
        }
        Insert: {
          action: string
          changed_by?: string | null
          created_at?: string
          field_name?: string | null
          history_id?: number
          new_value?: string | null
          old_value?: string | null
          ticket_id: number
        }
        Update: {
          action?: string
          changed_by?: string | null
          created_at?: string
          field_name?: string | null
          history_id?: number
          new_value?: string | null
          old_value?: string | null
          ticket_id?: number
        }
        Relationships: [
          { foreignKeyName: 'ticket_history_ticket_id_fkey'; columns: ['ticket_id']; isOneToOne: false; referencedRelation: 'tickets'; referencedColumns: ['ticket_id'] },
          { foreignKeyName: 'ticket_history_changed_by_fkey'; columns: ['changed_by']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
        ]
      }
      ticket_reopen_requests: {
        Row: {
          decided_at: string | null
          decided_by: string | null
          decision: string
          reason: string | null
          request_id: number
          requested_at: string
          requested_by: string
          ticket_id: number
        }
        Insert: {
          decided_at?: string | null
          decided_by?: string | null
          decision?: string
          reason?: string | null
          request_id?: number
          requested_at?: string
          requested_by: string
          ticket_id: number
        }
        Update: {
          decided_at?: string | null
          decided_by?: string | null
          decision?: string
          reason?: string | null
          request_id?: number
          requested_at?: string
          requested_by?: string
          ticket_id?: number
        }
        Relationships: [
          { foreignKeyName: 'ticket_reopen_requests_ticket_id_fkey'; columns: ['ticket_id']; isOneToOne: false; referencedRelation: 'tickets'; referencedColumns: ['ticket_id'] },
          { foreignKeyName: 'ticket_reopen_requests_requested_by_fkey'; columns: ['requested_by']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
          { foreignKeyName: 'ticket_reopen_requests_decided_by_fkey'; columns: ['decided_by']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
        ]
      }
      tickets: {
        Row: {
          ai_confidence: number | null
          ai_explanation: string | null
          ai_routing_status: string
          ai_suggested_department_id: number | null
          ai_suggested_issue_type_id: number | null
          ai_suggested_priority_id: number | null
          closed_at: string | null
          created_at: string
          deleted_at: string | null
          department_id: number | null
          description: string | null
          is_emergency: boolean
          issue_type_id: number | null
          location_id: number | null
          original_text: string
          original_title: string | null
          priority_id: number | null
          reported_by: string
          resolution_notes: string | null
          resolved_at: string | null
          status_id: number
          ticket_id: number
          title: string
          updated_at: string
        }
        Insert: {
          ai_confidence?: number | null
          ai_explanation?: string | null
          ai_routing_status?: string
          ai_suggested_department_id?: number | null
          ai_suggested_issue_type_id?: number | null
          ai_suggested_priority_id?: number | null
          closed_at?: string | null
          created_at?: string
          deleted_at?: string | null
          department_id?: number | null
          description?: string | null
          is_emergency?: boolean
          issue_type_id?: number | null
          location_id?: number | null
          original_text: string
          original_title?: string | null
          priority_id?: number | null
          reported_by: string
          resolution_notes?: string | null
          resolved_at?: string | null
          status_id: number
          ticket_id?: number
          title: string
          updated_at?: string
        }
        Update: {
          ai_confidence?: number | null
          ai_explanation?: string | null
          ai_routing_status?: string
          ai_suggested_department_id?: number | null
          ai_suggested_issue_type_id?: number | null
          ai_suggested_priority_id?: number | null
          closed_at?: string | null
          created_at?: string
          deleted_at?: string | null
          department_id?: number | null
          description?: string | null
          is_emergency?: boolean
          issue_type_id?: number | null
          location_id?: number | null
          original_text?: string
          original_title?: string | null
          priority_id?: number | null
          reported_by?: string
          resolution_notes?: string | null
          resolved_at?: string | null
          status_id?: number
          ticket_id?: number
          title?: string
          updated_at?: string
        }
        Relationships: [
          { foreignKeyName: 'tickets_reported_by_fkey'; columns: ['reported_by']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
          { foreignKeyName: 'tickets_department_id_fkey'; columns: ['department_id']; isOneToOne: false; referencedRelation: 'departments'; referencedColumns: ['department_id'] },
          { foreignKeyName: 'tickets_issue_type_id_fkey'; columns: ['issue_type_id']; isOneToOne: false; referencedRelation: 'issue_types'; referencedColumns: ['issue_type_id'] },
          { foreignKeyName: 'tickets_priority_id_fkey'; columns: ['priority_id']; isOneToOne: false; referencedRelation: 'priorities'; referencedColumns: ['priority_id'] },
          { foreignKeyName: 'tickets_status_id_fkey'; columns: ['status_id']; isOneToOne: false; referencedRelation: 'statuses'; referencedColumns: ['status_id'] },
          { foreignKeyName: 'tickets_location_id_fkey'; columns: ['location_id']; isOneToOne: false; referencedRelation: 'locations'; referencedColumns: ['location_id'] },
        ]
      }
      user_departments: {
        Row: {
          created_at: string
          department_id: number
          user_department_id: number
          user_id: string
        }
        Insert: {
          created_at?: string
          department_id: number
          user_department_id?: number
          user_id: string
        }
        Update: {
          created_at?: string
          department_id?: number
          user_department_id?: number
          user_id?: string
        }
        Relationships: [
          { foreignKeyName: 'user_departments_user_id_fkey'; columns: ['user_id']; isOneToOne: false; referencedRelation: 'users'; referencedColumns: ['user_id'] },
          { foreignKeyName: 'user_departments_department_id_fkey'; columns: ['department_id']; isOneToOne: false; referencedRelation: 'departments'; referencedColumns: ['department_id'] },
        ]
      }
      users: {
        Row: {
          active: boolean
          created_at: string
          email: string
          first_name: string
          last_name: string
          phone: string | null
          role_id: number
          student_type_id: number | null
          updated_at: string
          user_id: string
        }
        Insert: {
          active?: boolean
          created_at?: string
          email: string
          first_name: string
          last_name: string
          phone?: string | null
          role_id: number
          student_type_id?: number | null
          updated_at?: string
          user_id: string
        }
        Update: {
          active?: boolean
          created_at?: string
          email?: string
          first_name?: string
          last_name?: string
          phone?: string | null
          role_id?: number
          student_type_id?: number | null
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          { foreignKeyName: 'users_role_id_fkey'; columns: ['role_id']; isOneToOne: false; referencedRelation: 'roles'; referencedColumns: ['role_id'] },
          { foreignKeyName: 'users_student_type_id_fkey'; columns: ['student_type_id']; isOneToOne: false; referencedRelation: 'student_types'; referencedColumns: ['student_type_id'] },
        ]
      }
    }
    Views: Record<never, never>
    Functions: {
      admin_set_user_active: {
        Args: { p_active: boolean; p_user_id: string }
        Returns: undefined
      }
      admin_set_user_role: {
        Args: { p_role: string; p_user_id: string }
        Returns: undefined
      }
      assign_ticket: {
        Args: { p_assignee: string; p_ticket_id: number }
        Returns: undefined
      }
      decide_reopen: {
        Args: { p_approve: boolean; p_note?: string; p_request_id: number }
        Returns: undefined
      }
      set_department_member: {
        Args: { p_department_id: number; p_member: boolean; p_user_id: string }
        Returns: undefined
      }
      transfer_ticket: {
        Args: { p_department_id: number; p_reason?: string; p_ticket_id: number }
        Returns: undefined
      }
      unassign_ticket: { Args: { p_ticket_id: number }; Returns: undefined }
      withdraw_own_ticket: { Args: { p_ticket_id: number }; Returns: undefined }
    }
    Enums: Record<never, never>
    CompositeTypes: Record<never, never>
  }
}

type PublicSchema = Database['public']

export type Tables<T extends keyof PublicSchema['Tables']> =
  PublicSchema['Tables'][T]['Row']

export type TablesInsert<T extends keyof PublicSchema['Tables']> =
  PublicSchema['Tables'][T]['Insert']

export type TablesUpdate<T extends keyof PublicSchema['Tables']> =
  PublicSchema['Tables'][T]['Update']
